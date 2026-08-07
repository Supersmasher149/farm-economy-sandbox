/* Fused per-plot daily physics kernel.
 *
 * This is a drop-in replacement for the per-plot loop in
 * simulation/weather.py:apply_weather together with the whole of
 * simulation/crop_growth.py:update_crop_stress, which together are ~20% of
 * batch runtime and are the only hot region of the simulator that is a tight
 * numeric loop with no agent callback, no RNG draw, and no config parsing.
 *
 * The pure-Python versions remain the reference implementation. They are what
 * runs when this module is not built, and
 * tests/test_fastplot_equivalence.py asserts the two produce bit-identical
 * plot state. If the two ever disagree, the Python side is right.
 *
 * REPLAY IS LOAD-BEARING. `main.py replay` and the committed golden baseline
 * require that a given seed reproduces a run exactly, so every floating point
 * operation below must match what CPython's bytecode would have done:
 *
 *   - Neumaier compensated summation for the nutrient shortfall, because
 *     since 3.12 builtin sum() applies it to floats. Plain accumulation
 *     changes the last bits and silently breaks every recorded seed.
 *   - The literal max/min forms, not fmax/fmin and not comparison chains,
 *     so +0.0 vs -0.0 and the tie-goes-to-the-first-argument behaviour of
 *     Python's two-argument max()/min() are preserved exactly.
 *   - Expression grouping is preserved as written in the Python source;
 *     `(m + rainfall) + regen` is not the same double as `m + (rainfall +
 *     regen)`.
 *   - Must be compiled without -ffast-math and with -ffp-contract=off, or
 *     the compiler will contract these into FMAs and change results.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>

/* Bump when the layout of CropProfile.flat changes (simulation/derived.py).
 * The Python wrapper compares this against its own expectation at import
 * time and refuses to use the kernel on a mismatch, so a stale compiled
 * .so can never silently read fields in the wrong order. */
#define PROFILE_LAYOUT 1

#define PROFILE_FIELDS 9

/* Interned attribute names, created once at module init. */
static PyObject *s_moisture, *s_nitrogen, *s_phosphorus, *s_potassium, *s_ph,
    *s_soil_health, *s_pest_pressure, *s_disease_pressure, *s_crop, *s_crop_id,
    *s_last_watered_day, *s_neglect_days, *s_water_stress, *s_nutrient_stress,
    *s_temperature_stress, *s_pest_stress, *s_disease_stress;

/* Python's `min(a, x)` returns x only when x < a; `max(a, x)` only when
 * x > a. Written this way (rather than fmin/fmax) so ties and signed zeros
 * behave exactly as the interpreter would. */
static inline double py_min(double limit, double x) { return (x < limit) ? x : limit; }
static inline double py_max(double limit, double x) { return (x > limit) ? x : limit; }

/* max(0.0, min(1.0, x)) -- the clamp spelled out across the Python source. */
static inline double clamp01(double x) { return py_max(0.0, py_min(1.0, x)); }

static int get_double(PyObject *obj, PyObject *name, double *out)
{
    PyObject *value = PyObject_GetAttr(obj, name);
    if (value == NULL) {
        return -1;
    }
    double result = PyFloat_AsDouble(value);
    Py_DECREF(value);
    if (result == -1.0 && PyErr_Occurred()) {
        return -1;
    }
    *out = result;
    return 0;
}

static int set_double(PyObject *obj, PyObject *name, double value)
{
    PyObject *boxed = PyFloat_FromDouble(value);
    if (boxed == NULL) {
        return -1;
    }
    int rc = PyObject_SetAttr(obj, name, boxed);
    Py_DECREF(boxed);
    return rc;
}

static int incr_double(PyObject *obj, PyObject *name, double delta)
{
    double current;
    if (get_double(obj, name, &current) < 0) {
        return -1;
    }
    return set_double(obj, name, current + delta);
}

/* Exactly CPython's builtin sum() float path (Objects/bltinmodule.c):
 * Neumaier compensated summation, starting from the integer 0 that sum()
 * uses as its default start value. */
static inline double neumaier_sum(const double *values, Py_ssize_t count)
{
    double total = 0.0;
    double compensation = 0.0;
    for (Py_ssize_t i = 0; i < count; i++) {
        double x = values[i];
        double t = total + x;
        if (fabs(total) >= fabs(x)) {
            compensation += (total - t) + x;
        } else {
            compensation += (x - t) + total;
        }
        total = t;
    }
    return total + compensation;
}

/* Mirrors crop_growth.update_crop_stress. `moisture` is passed in and out
 * because apply_day keeps it in a register across the fused loop rather than
 * writing it to the plot twice. */
static int update_crop_stress(PyObject *plot, PyObject *planted, PyObject *flat, double temperature,
                              double evaporation, double pest_pressure, double disease_pressure,
                              double *moisture)
{
    double min_moisture = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 0));
    double ph_low = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 1));
    double ph_high = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 2));
    double temperature_low = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 3));
    double temperature_high = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 4));
    double pest_susceptibility = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 5));
    double disease_susceptibility = PyFloat_AsDouble(PyTuple_GET_ITEM(flat, 6));
    PyObject *needs = PyTuple_GET_ITEM(flat, 8);
    if (PyErr_Occurred()) {
        return -1;
    }

    if (incr_double(planted, s_water_stress, py_max(0.0, min_moisture - *moisture)) < 0) {
        return -1;
    }

    /* Shortfall is computed against the nutrient levels as they stand before
     * any depletion below, which is also the order the Python generator
     * observes them in. */
    Py_ssize_t nutrient_count = PyTuple_GET_SIZE(needs);
    double shortfalls[8];
    if (nutrient_count > 8) {
        PyErr_SetString(PyExc_ValueError, "_fastplot supports at most 8 nutrients");
        return -1;
    }
    for (Py_ssize_t i = 0; i < nutrient_count; i++) {
        PyObject *pair = PyTuple_GET_ITEM(needs, i);
        PyObject *name = PyTuple_GET_ITEM(pair, 0);
        double amount = PyFloat_AsDouble(PyTuple_GET_ITEM(pair, 1));
        double level;
        if (get_double(plot, name, &level) < 0) {
            return -1;
        }
        shortfalls[i] = py_max(0.0, amount - level);
    }
    double nutrient_shortfall = neumaier_sum(shortfalls, nutrient_count);

    double ph;
    if (get_double(plot, s_ph, &ph) < 0) {
        return -1;
    }
    double ph_stress = 0.0;
    if (ph < ph_low) {
        ph_stress = (ph_low - ph) * 0.1;
    } else if (ph > ph_high) {
        ph_stress = (ph - ph_high) * 0.1;
    }
    if (incr_double(planted, s_nutrient_stress, nutrient_shortfall + ph_stress) < 0) {
        return -1;
    }

    if (temperature < temperature_low) {
        if (incr_double(planted, s_temperature_stress, (temperature_low - temperature) / 20) < 0) {
            return -1;
        }
    } else if (temperature > temperature_high) {
        if (incr_double(planted, s_temperature_stress, (temperature - temperature_high) / 20) < 0) {
            return -1;
        }
    }

    if (incr_double(planted, s_pest_stress, pest_pressure * pest_susceptibility) < 0) {
        return -1;
    }
    if (incr_double(planted, s_disease_stress, disease_pressure * disease_susceptibility) < 0) {
        return -1;
    }

    *moisture = clamp01(*moisture - evaporation);

    /* Re-read rather than reusing the levels captured above: a config could
     * in principle name a nutrient after a field the moisture write touched,
     * and the Python loop would see the updated value there. */
    for (Py_ssize_t i = 0; i < nutrient_count; i++) {
        PyObject *pair = PyTuple_GET_ITEM(needs, i);
        PyObject *name = PyTuple_GET_ITEM(pair, 0);
        double amount = PyFloat_AsDouble(PyTuple_GET_ITEM(pair, 1));
        double level;
        if (get_double(plot, name, &level) < 0) {
            return -1;
        }
        if (set_double(plot, name, clamp01(level - amount)) < 0) {
            return -1;
        }
    }
    return 0;
}

PyDoc_STRVAR(apply_day_doc,
             "apply_day(plots, day, rainfall, evaporation, temperature, regen, dynamics, profiles)\n"
             "\n"
             "Fused per-plot daily update. Equivalent to the plot loop in\n"
             "weather.apply_weather plus crop_growth.update_crop_stress.");

static PyObject *apply_day(PyObject *Py_UNUSED(self), PyObject *args)
{
    PyObject *plots, *regen, *dynamics, *profiles;
    long day;
    double rainfall, evaporation, temperature;

    if (!PyArg_ParseTuple(args, "OldddOOO", &plots, &day, &rainfall, &evaporation, &temperature,
                          &regen, &dynamics, &profiles)) {
        return NULL;
    }
    if (!PyList_Check(plots)) {
        PyErr_SetString(PyExc_TypeError, "plots must be a list");
        return NULL;
    }
    if (!PyTuple_Check(regen) || PyTuple_GET_SIZE(regen) != 7) {
        PyErr_SetString(PyExc_TypeError, "regen must be a 7-tuple");
        return NULL;
    }
    if (!PyTuple_Check(dynamics) || PyTuple_GET_SIZE(dynamics) != 7) {
        PyErr_SetString(PyExc_TypeError, "dynamics must be a 7-tuple");
        return NULL;
    }
    if (!PyDict_Check(profiles)) {
        PyErr_SetString(PyExc_TypeError, "profiles must be a dict");
        return NULL;
    }

    double regen_moisture = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 0));
    double regen_n = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 1));
    double regen_p = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 2));
    double regen_k = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 3));
    double regen_soil_health = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 4));
    double regen_pest = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 5));
    double regen_disease = PyFloat_AsDouble(PyTuple_GET_ITEM(regen, 6));

    double fallow_pest_decay = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 0));
    double fallow_disease_decay = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 1));
    double fallow_soil_health_regen = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 2));
    double max_disease_pressure = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 3));
    double disease_growth_per_rainfall = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 4));
    double max_pest_pressure = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 5));
    double pest_growth_per_day = PyFloat_AsDouble(PyTuple_GET_ITEM(dynamics, 6));
    if (PyErr_Occurred()) {
        return NULL;
    }

    /* Python evaluates `regen_n or regen_p or regen_k` for truthiness. */
    int regenerates_nutrients = (regen_n != 0.0) || (regen_p != 0.0) || (regen_k != 0.0);

    Py_ssize_t plot_count = PyList_GET_SIZE(plots);
    for (Py_ssize_t i = 0; i < plot_count; i++) {
        PyObject *plot = PyList_GET_ITEM(plots, i);

        double moisture, soil_health, pest_pressure, disease_pressure;
        if (get_double(plot, s_moisture, &moisture) < 0 ||
            get_double(plot, s_soil_health, &soil_health) < 0 ||
            get_double(plot, s_pest_pressure, &pest_pressure) < 0 ||
            get_double(plot, s_disease_pressure, &disease_pressure) < 0) {
            return NULL;
        }

        /* Grouping preserved: (moisture + rainfall) + regen_moisture. */
        moisture = py_min(1.0, (moisture + rainfall) + regen_moisture);

        if (regenerates_nutrients) {
            /* Each field is only written when its regen is non-zero, exactly
             * as the Python branches do -- an untouched attribute must stay
             * the identical object it already was. */
            if (regen_n != 0.0) {
                double level;
                if (get_double(plot, s_nitrogen, &level) < 0 ||
                    set_double(plot, s_nitrogen, py_min(1.0, level + regen_n)) < 0) {
                    return NULL;
                }
            }
            if (regen_p != 0.0) {
                double level;
                if (get_double(plot, s_phosphorus, &level) < 0 ||
                    set_double(plot, s_phosphorus, py_min(1.0, level + regen_p)) < 0) {
                    return NULL;
                }
            }
            if (regen_k != 0.0) {
                double level;
                if (get_double(plot, s_potassium, &level) < 0 ||
                    set_double(plot, s_potassium, py_min(1.0, level + regen_k)) < 0) {
                    return NULL;
                }
            }
        }
        if (regen_soil_health != 0.0) {
            soil_health = py_min(1.0, soil_health + regen_soil_health);
        }
        if (regen_pest != 0.0) {
            pest_pressure = py_max(0.0, pest_pressure - regen_pest);
        }
        if (regen_disease != 0.0) {
            disease_pressure = py_max(0.0, disease_pressure - regen_disease);
        }

        PyObject *planted = PyObject_GetAttr(plot, s_crop);
        if (planted == NULL) {
            return NULL;
        }

        if (planted == Py_None) {
            Py_DECREF(planted);
            moisture = clamp01(moisture - evaporation);
            pest_pressure = py_max(0.0, pest_pressure * fallow_pest_decay);
            disease_pressure = py_max(0.0, disease_pressure * fallow_disease_decay);
            soil_health = py_min(1.0, soil_health + fallow_soil_health_regen);
            if (set_double(plot, s_moisture, moisture) < 0 ||
                set_double(plot, s_soil_health, soil_health) < 0 ||
                set_double(plot, s_pest_pressure, pest_pressure) < 0 ||
                set_double(plot, s_disease_pressure, disease_pressure) < 0) {
                return NULL;
            }
            continue;
        }

        PyObject *crop_id = PyObject_GetAttr(planted, s_crop_id);
        if (crop_id == NULL) {
            Py_DECREF(planted);
            return NULL;
        }
        /* Matches the Python `crops_by_id[crop_id]`: a missing crop is a
         * KeyError, not a silent skip. */
        PyObject *flat = PyDict_GetItemWithError(profiles, crop_id);
        if (flat == NULL) {
            if (!PyErr_Occurred()) {
                PyErr_SetObject(PyExc_KeyError, crop_id);
            }
            Py_DECREF(crop_id);
            Py_DECREF(planted);
            return NULL;
        }
        Py_DECREF(crop_id);
        if (!PyTuple_Check(flat) || PyTuple_GET_SIZE(flat) != PROFILE_FIELDS) {
            PyErr_SetString(PyExc_TypeError, "profile entry must be a 9-tuple");
            Py_DECREF(planted);
            return NULL;
        }

        if (update_crop_stress(plot, planted, flat, temperature, evaporation, pest_pressure,
                               disease_pressure, &moisture) < 0) {
            Py_DECREF(planted);
            return NULL;
        }

        /* neglect_days is integer arithmetic: water_interval_days is
         * validated as an integer (simulation/configuration.py) and
         * last_watered_day is always set by PlantedCrop.__post_init__. */
        long interval = PyLong_AsLong(PyTuple_GET_ITEM(flat, 7));
        if (interval == -1 && PyErr_Occurred()) {
            Py_DECREF(planted);
            return NULL;
        }
        PyObject *last_watered = PyObject_GetAttr(planted, s_last_watered_day);
        if (last_watered == NULL) {
            Py_DECREF(planted);
            return NULL;
        }
        long last_watered_day = PyLong_AsLong(last_watered);
        Py_DECREF(last_watered);
        if (last_watered_day == -1 && PyErr_Occurred()) {
            Py_DECREF(planted);
            return NULL;
        }
        long overdue = day - last_watered_day - interval;
        PyObject *neglect = PyLong_FromLong(overdue > 0 ? overdue : 0);
        if (neglect == NULL || PyObject_SetAttr(planted, s_neglect_days, neglect) < 0) {
            Py_XDECREF(neglect);
            Py_DECREF(planted);
            return NULL;
        }
        Py_DECREF(neglect);
        Py_DECREF(planted);

        disease_pressure = py_min(max_disease_pressure,
                                  disease_pressure + rainfall * disease_growth_per_rainfall);
        pest_pressure = py_min(max_pest_pressure, pest_pressure + pest_growth_per_day);

        if (set_double(plot, s_moisture, moisture) < 0 ||
            set_double(plot, s_pest_pressure, pest_pressure) < 0 ||
            set_double(plot, s_disease_pressure, disease_pressure) < 0) {
            return NULL;
        }
        /* soil_health is only written when something changed it; the planted
         * branch never does, so skip the store. */
        if (regen_soil_health != 0.0) {
            if (set_double(plot, s_soil_health, soil_health) < 0) {
                return NULL;
            }
        }
    }

    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"apply_day", apply_day, METH_VARARGS, apply_day_doc},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "simulation._fastplot",
    "Fused per-plot daily physics kernel (optional accelerator).",
    -1,
    methods,
    NULL,
    NULL,
    NULL,
    NULL,
};

#define INTERN(var, name)                 \
    do {                                  \
        (var) = PyUnicode_InternFromString(name); \
        if ((var) == NULL) {              \
            return NULL;                  \
        }                                 \
    } while (0)

PyMODINIT_FUNC PyInit__fastplot(void)
{
    INTERN(s_moisture, "moisture");
    INTERN(s_nitrogen, "nitrogen");
    INTERN(s_phosphorus, "phosphorus");
    INTERN(s_potassium, "potassium");
    INTERN(s_ph, "ph");
    INTERN(s_soil_health, "soil_health");
    INTERN(s_pest_pressure, "pest_pressure");
    INTERN(s_disease_pressure, "disease_pressure");
    INTERN(s_crop, "crop");
    INTERN(s_crop_id, "crop_id");
    INTERN(s_last_watered_day, "last_watered_day");
    INTERN(s_neglect_days, "neglect_days");
    INTERN(s_water_stress, "water_stress");
    INTERN(s_nutrient_stress, "nutrient_stress");
    INTERN(s_temperature_stress, "temperature_stress");
    INTERN(s_pest_stress, "pest_stress");
    INTERN(s_disease_stress, "disease_stress");

    PyObject *module = PyModule_Create(&moduledef);
    if (module == NULL) {
        return NULL;
    }
    if (PyModule_AddIntConstant(module, "PROFILE_LAYOUT", PROFILE_LAYOUT) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
