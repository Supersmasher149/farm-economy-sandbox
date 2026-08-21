#include "config.h"

#include "cJSON.h"

#include <errno.h>
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    const char *name;
    cJSON *json;
} Document;

static ConfigError *g_error;
static const char *g_file;

static bool errorf(ConfigErrorCode code, const char *path, const char *detail) {
    if (g_error != NULL) {
        g_error->code = code;
        snprintf(g_error->message, sizeof(g_error->message), "%s: %s%s%s",
                 g_file != NULL ? g_file : "configuration", path != NULL ? path : "",
                 path != NULL && detail != NULL ? ": " : "", detail != NULL ? detail : "");
    }
    return false;
}

static bool alloc_error(void) { return errorf(CONFIG_ERROR_ALLOCATION, "", "allocation failed"); }

static cJSON *member(cJSON *object, const char *key, const char *path, bool required) {
    cJSON *value = cJSON_GetObjectItemCaseSensitive(object, key);
    if (value == NULL && required) {
        errorf(CONFIG_ERROR_SCHEMA, path, "required field is missing");
    }
    return value;
}

static bool object(cJSON *value, const char *path) {
    return value != NULL && cJSON_IsObject(value) ? true : errorf(CONFIG_ERROR_SCHEMA, path, "must be an object");
}

static bool array(cJSON *value, const char *path) {
    return value != NULL && cJSON_IsArray(value) ? true : errorf(CONFIG_ERROR_SCHEMA, path, "must be an array");
}

static bool number(cJSON *value, const char *path, double minimum, double maximum, bool exclusive_min) {
    if (value == NULL || !cJSON_IsNumber(value) || !isfinite(value->valuedouble))
        return errorf(CONFIG_ERROR_SCHEMA, path, "must be a finite number");
    if ((exclusive_min ? value->valuedouble <= minimum : value->valuedouble < minimum) ||
        (maximum >= 0.0 && value->valuedouble > maximum))
        return errorf(CONFIG_ERROR_RANGE, path, "value is outside the allowed range");
    return true;
}

static bool integer(cJSON *value, const char *path, int minimum) {
    if (value == NULL || !cJSON_IsNumber(value) || !isfinite(value->valuedouble) ||
        value->valuedouble != floor(value->valuedouble) || value->valuedouble < INT_MIN ||
        value->valuedouble > INT_MAX)
        return errorf(CONFIG_ERROR_SCHEMA, path, "must be an integer");
    if (value->valueint < minimum) return errorf(CONFIG_ERROR_RANGE, path, "value is too small");
    return true;
}

/* cJSON stores all JSON numbers as doubles, so only integers through 2^53-1
 * can be recovered exactly for the optional configuration seed. The CLI/API
 * RunSeed path accepts the full uint64 range; rejecting larger JSON tokens is
 * safer than silently changing the seed during a double conversion. */
static bool seed_integer(cJSON *value, const char *path, uint64_t *out) {
    if (value == NULL || !cJSON_IsNumber(value) || !isfinite(value->valuedouble) ||
        value->valuedouble < 0.0 || value->valuedouble != floor(value->valuedouble) ||
        value->valuedouble > 9007199254740991.0) {
        return errorf(CONFIG_ERROR_SCHEMA, path,
                       "must be a non-negative exactly representable integer");
    }
    *out = (uint64_t)value->valuedouble;
    return true;
}

static const char *string(cJSON *value, const char *path) {
    if (value == NULL || !cJSON_IsString(value) || value->valuestring == NULL ||
        value->valuestring[0] == '\0' || value->valuestring[strspn(value->valuestring, " \t\r\n")] == '\0') {
        errorf(CONFIG_ERROR_SCHEMA, path, "must be a non-empty string");
        return NULL;
    }
    return value->valuestring;
}

static bool enum_value(cJSON *value, const char *path, const char *const *values) {
    const char *s = string(value, path);
    if (s == NULL) return false;
    for (size_t i = 0; values[i] != NULL; i++) if (strcmp(s, values[i]) == 0) return true;
    return errorf(CONFIG_ERROR_SCHEMA, path, "invalid enum value");
}

static bool only(cJSON *object_value, const char *path, const char *const *allowed) {
    cJSON *item;
    cJSON_ArrayForEach(item, object_value) {
        bool found = false;
        for (size_t i = 0; allowed[i] != NULL; i++) if (strcmp(item->string, allowed[i]) == 0) found = true;
        if (!found) return errorf(CONFIG_ERROR_SCHEMA, path, "contains an unknown field");
    }
    return true;
}

static char *copy_string(const char *value) {
    size_t n = strlen(value) + 1;
    char *copy = malloc(n);
    if (copy != NULL) memcpy(copy, value, n);
    return copy;
}

static bool dup_field(cJSON *object_value, const char *key, const char *path, char **out, bool required) {
    cJSON *member_value = member(object_value, key, path, required);
    if ((member_value == NULL || cJSON_IsNull(member_value)) && !required) return true;
    const char *value = string(member_value, path);
    if (value == NULL) return false;
    *out = copy_string(value);
    return *out != NULL ? true : alloc_error();
}

static Quality quality(const char *value) {
    if (strcmp(value, "processing") == 0) return QUALITY_PROCESSING;
    if (strcmp(value, "standard") == 0) return QUALITY_STANDARD;
    if (strcmp(value, "premium") == 0) return QUALITY_PREMIUM;
    return QUALITY_REJECTED;
}

static const char *const QUALITY_NAMES[] = {"rejected", "processing", "standard", "premium", NULL};

static int season(const char *value) {
    static const char *const names[] = {"spring", "summer", "autumn", "winter"};
    for (int i = 0; i < 4; i++) if (strcmp(value, names[i]) == 0) return i;
    return -1;
}

static bool range2(cJSON *value, const char *path, bool integer_values, double minimum, double maximum) {
    if (!array(value, path) || cJSON_GetArraySize(value) != 2)
        return errorf(CONFIG_ERROR_SCHEMA, path, "must contain exactly two values");
    cJSON *a = cJSON_GetArrayItem(value, 0), *b = cJSON_GetArrayItem(value, 1);
    if (integer_values ? (!integer(a, path, (int)minimum) || !integer(b, path, (int)minimum)) :
        (!number(a, path, minimum, maximum, false) || !number(b, path, minimum, maximum, false))) return false;
    if (a->valuedouble > b->valuedouble) return errorf(CONFIG_ERROR_RANGE, path, "range is not ordered");
    return true;
}

static bool read_document(const char *directory, const char *name, Document *doc) {
    char path[PATH_MAX];
    int path_length = snprintf(path, sizeof(path), "%s/%s.json", directory, name);
    if (path_length < 0 || (size_t)path_length >= sizeof(path)) {
        g_file = name;
        return errorf(CONFIG_ERROR_IO, "", "configuration path is too long");
    }
    g_file = name;
    FILE *file = fopen(path, "rb");
    if (file == NULL) return errorf(CONFIG_ERROR_IO, "", strerror(errno));
    if (fseek(file, 0, SEEK_END) != 0) { fclose(file); return errorf(CONFIG_ERROR_IO, "", "cannot seek file"); }
    long length = ftell(file);
    if (length < 0 || (unsigned long)length > SIZE_MAX - 1) { fclose(file); return errorf(CONFIG_ERROR_IO, "", "invalid file size"); }
    rewind(file);
    char *text = malloc((size_t)length + 1);
    if (text == NULL) { fclose(file); return alloc_error(); }
    size_t read = fread(text, 1, (size_t)length, file); fclose(file); text[read] = '\0';
    if (read != (size_t)length) { free(text); return errorf(CONFIG_ERROR_IO, "", "cannot read file"); }
    doc->json = cJSON_Parse(text); free(text);
    if (doc->json == NULL) return errorf(CONFIG_ERROR_JSON, "", "malformed JSON");
    doc->name = name; return true;
}

static bool parse_nutrients(cJSON *value, const char *path, NutrientDemand *out,
                            NutrientDemand defaults, double maximum) {
    static const char *const allowed[] = {"nitrogen", "phosphorus", "potassium", NULL};
    *out = value == NULL ? defaults : (NutrientDemand){0};
    if (value == NULL) return true;
    if (!object(value, path) || !only(value, path, allowed)) return false;
    const char *keys[] = {"nitrogen", "phosphorus", "potassium"};
    double *fields[] = {&out->nitrogen, &out->phosphorus, &out->potassium};
    for (int i = 0; i < 3; i++) if (cJSON_GetObjectItem(value, keys[i]) != NULL) {
        if (!number(cJSON_GetObjectItem(value, keys[i]), path, 0, maximum, false)) return false;
        *fields[i] = cJSON_GetObjectItem(value, keys[i])->valuedouble;
    }
    return true;
}

static bool parse_seasonal(cJSON *value, const char *path, double out[SEASON_COUNT]) {
    static const char *const names[] = {"spring", "summer", "autumn", "winter", NULL};
    for (int i = 0; i < SEASON_COUNT; i++) out[i] = 1.0;
    if (value == NULL) return true;
    if (!object(value, path) || !only(value, path, names)) return false;
    cJSON *item;
    cJSON_ArrayForEach(item, value) {
        int s = season(item->string); char p[128]; snprintf(p, sizeof(p), "%s.%s", path, item->string);
        if (!number(item, p, 0, -1, false)) return false; out[s] = item->valuedouble;
    }
    return true;
}

static bool parse_world(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *root = d->json;
    if (!array(root, d->name)) return false;
    size_t n = (size_t)cJSON_GetArraySize(root);
    if (n == 0) return errorf(CONFIG_ERROR_SCHEMA, d->name, "at least one crop is required");
    c->crop_count = n; c->crops = n ? calloc(n, sizeof(*c->crops)) : NULL;
    if (n && c->crops == NULL) return alloc_error();
    c->item_count = n; c->items = n ? calloc(n, sizeof(*c->items)) : NULL;
    if (n && c->items == NULL) return alloc_error();
    cJSON *item; size_t i = 0;
    static const char *const crop_fields[] = {"id","name","role","family","seed_cost","growth_days","min_yield","max_yield","base_price","price_variation","loss_chance","water_interval_days","unlock_requirement","shelf_life_days","temperature_range","ph_range","min_moisture","pest_susceptibility","disease_susceptibility","nutrient_demand","seasonal_demand","processing_value",NULL};
    static const char *const roles[] = {"fast", "standard", "premium", NULL};
    cJSON_ArrayForEach(item, root) {
        char p[128]; snprintf(p, sizeof(p), "crops[%zu]", i); if (!object(item, p) || !only(item, p, crop_fields)) return false;
        CropDef *crop = &c->crops[i]; const char *id = string(member(item,"id",p,true), p); if (!id) return false;
        const char *crop_name = string(member(item, "name", p, true), p); if (!crop_name) return false;
        crop->item_id = (ItemId)i; crop->role = CROP_ROLE_OTHER;
        const char *role = cJSON_GetObjectItem(item,"role") ? string(cJSON_GetObjectItem(item,"role"), p) : NULL;
        if (role && !enum_value(cJSON_GetObjectItem(item,"role"), p, roles)) return false;
        if (role && strcmp(role,"standard")==0) crop->role=CROP_ROLE_STANDARD; else if(role&&strcmp(role,"fast")==0) crop->role=CROP_ROLE_FAST;
        if (!dup_field(item,"family",p,(char **)&crop->family,false)) return false;
        cJSON *v; double base_price, price_variation;
#define CNUM(k,dst,min,max) do { v=member(item,k,p,true); if(!number(v,p,min,max,false)) return false; (dst)=v->valuedouble; } while(0)
#define CINT(k,dst,min) do { v=member(item,k,p,true); if(!integer(v,p,min)) return false; (dst)=v->valueint; } while(0)
        CNUM("seed_cost",crop->seed_cost,0,-1); CINT("growth_days",crop->growth_days,1); CINT("min_yield",crop->min_yield,0); CINT("max_yield",crop->max_yield,0); if(crop->min_yield>crop->max_yield)return errorf(CONFIG_ERROR_RANGE,p,"yield range is not ordered");
        CNUM("base_price",base_price,0,-1); CNUM("price_variation",price_variation,0,1); CNUM("loss_chance",crop->loss_chance,0,1); CINT("water_interval_days",crop->water_interval_days,1);
        crop->shelf_life_days=7; v=cJSON_GetObjectItem(item,"shelf_life_days"); if(v && !integer(v,p,1))return false; if(v)crop->shelf_life_days=v->valueint;
        crop->temperature_low=10;crop->temperature_high=30;v=cJSON_GetObjectItem(item,"temperature_range");if(v&&!range2(v,p,false,-DBL_MAX,DBL_MAX))return false;if(v){crop->temperature_low=cJSON_GetArrayItem(v,0)->valuedouble;crop->temperature_high=cJSON_GetArrayItem(v,1)->valuedouble;}
        crop->ph_low=5.8;crop->ph_high=7;v=cJSON_GetObjectItem(item,"ph_range");if(v&&!range2(v,p,false,0,14))return false;if(v){crop->ph_low=cJSON_GetArrayItem(v,0)->valuedouble;crop->ph_high=cJSON_GetArrayItem(v,1)->valuedouble;}
        crop->min_moisture=.35;crop->pest_susceptibility=1;crop->disease_susceptibility=1; v=cJSON_GetObjectItem(item,"min_moisture");if(v&&!number(v,p,0,1,false))return false;if(v)crop->min_moisture=v->valuedouble;v=cJSON_GetObjectItem(item,"pest_susceptibility");if(v&&!number(v,p,0,-1,false))return false;if(v)crop->pest_susceptibility=v->valuedouble;v=cJSON_GetObjectItem(item,"disease_susceptibility");if(v&&!number(v,p,0,-1,false))return false;if(v)crop->disease_susceptibility=v->valuedouble;
        if(!parse_nutrients(cJSON_GetObjectItem(item,"nutrient_demand"),p,&crop->nutrient_demand,DEFAULT_NUTRIENT_DEMAND,-1)) return false;
        ItemDef *item_def = &c->items[i];
        item_def->id = (ItemId)i; item_def->type = ITEM_CROP;
        item_def->external_id = copy_string(id); item_def->name = copy_string(crop_name);
        if (item_def->external_id == NULL || item_def->name == NULL) return alloc_error();
        for (size_t j = 0; j < i; j++) if (strcmp(c->items[j].external_id, id) == 0)
            return errorf(CONFIG_ERROR_SCHEMA, p, "duplicate crop id");
        item_def->base_price = base_price; item_def->price_variation = price_variation;
        if (!parse_seasonal(cJSON_GetObjectItem(item,"seasonal_demand"),p,item_def->seasonal_demand)) return false;
        i++;
    }
#undef CNUM
#undef CINT
    return true;
}

static bool parse_upgrades(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    static const char *const fields[]={"id","name","description","cost","effect",NULL};
    static const char *const effects[]={"capacity","growth_time_reduction","storage","processing_capacity",NULL};
    cJSON *root=d->json; if(!array(root,"upgrades"))return false; size_t n=(size_t)cJSON_GetArraySize(root);
    c->upgrade_count=n;c->upgrades=n?calloc(n,sizeof(*c->upgrades)):NULL;if(n&&!c->upgrades)return alloc_error();
    cJSON *x; size_t i=0; cJSON_ArrayForEach(x,root){char p[96];snprintf(p,sizeof(p),"upgrades[%zu]",i);if(!object(x,p)||!only(x,p,fields))return false;
        const char *id=string(member(x,"id",p,true),p),*name=string(member(x,"name",p,true),p);if(!id||!name)return false;
        for(size_t j=0;j<i;j++)if(strcmp(c->upgrades[j].external_id,id)==0)return errorf(CONFIG_ERROR_SCHEMA,p,"duplicate id");
        c->upgrades[i].id=(UpgradeId)i;c->upgrades[i].external_id=copy_string(id);c->upgrades[i].name=copy_string(name);if(!c->upgrades[i].external_id||!c->upgrades[i].name)return alloc_error();
        cJSON *v=member(x,"cost",p,true);if(!number(v,p,0,-1,false))return false;c->upgrades[i].cost=v->valuedouble;cJSON *e=member(x,"effect",p,true);if(!object(e,p))return false;
        const char *et=string(member(e,"type",p,true),p);if(!et||!enum_value(member(e,"type",p,true),p,effects))return false;
        const char *const *effect_fields = strcmp(et,"storage")==0 ? (const char *const[]){"type","capacity_bonus","shelf_life_multiplier",NULL} : (const char *const[]){"type","amount",NULL};
        if(!only(e,p,effect_fields))return false;
        if(strcmp(et,"capacity")==0){c->upgrades[i].effect.type=EFFECT_CAPACITY;v=member(e,"amount",p,true);if(!integer(v,p,1))return false;c->upgrades[i].effect.as.capacity=v->valueint;}
        else if(strcmp(et,"processing_capacity")==0){c->upgrades[i].effect.type=EFFECT_PROCESSING_CAPACITY;v=member(e,"amount",p,true);if(!integer(v,p,1))return false;c->upgrades[i].effect.as.processing_capacity=v->valueint;}
         else if(strcmp(et,"growth_time_reduction")==0){c->upgrades[i].effect.type=EFFECT_GROWTH_TIME_REDUCTION;v=member(e,"amount",p,true);if(!number(v,p,0,-1,false)||v->valuedouble>=1.0)return errorf(CONFIG_ERROR_RANGE,p,"value is outside the allowed range");c->upgrades[i].effect.as.growth_time_reduction=v->valuedouble;}
        else {c->upgrades[i].effect.type=EFFECT_STORAGE;v=member(e,"capacity_bonus",p,true);if(!integer(v,p,0))return false;c->upgrades[i].effect.as.storage.capacity_bonus=v->valueint;v=member(e,"shelf_life_multiplier",p,true);if(!number(v,p,0,-1,true))return false;c->upgrades[i].effect.as.storage.shelf_life_multiplier=v->valuedouble;}
        i++;
    } return true;
}

static bool parse_processing(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *root = d->json;
    static const char *const top[] = {"base_capacity", "products", "recipes", NULL};
    if (!object(root, "processing") || !only(root, "processing", top)) return false;

    cJSON *v = member(root, "base_capacity", "processing.base_capacity", true);
    if (!integer(v, "processing.base_capacity", 0)) return false;
    c->processing_capacity = v->valueint;

    cJSON *products = cJSON_GetObjectItem(root, "products");
    if (products != NULL && !array(products, "processing.products")) return false;
    size_t pn = products == NULL ? 0 : (size_t)cJSON_GetArraySize(products);
    size_t old = c->item_count;
    if (old > SIZE_MAX / sizeof(*c->items) ||
        pn > SIZE_MAX / sizeof(*c->items) - old) return alloc_error();
    if (pn > 0) {
        ItemDef *items = realloc(c->items, (old + pn) * sizeof(*c->items));
        if (items == NULL) return alloc_error();
        c->items = items;
        memset(c->items + old, 0, pn * sizeof(*c->items));
    }
    c->item_count = old + pn;

    static const char *const pf[] = {
        "id", "name", "processed_base_price", "price_variation", "seasonal_demand", NULL};
    cJSON *x;
    size_t i = 0;
    cJSON_ArrayForEach(x, products) {
        char p[128];
        snprintf(p, sizeof(p), "processing.products[%zu]", i);
        if (!object(x, p) || !only(x, p, pf)) return false;
        const char *id = string(member(x, "id", p, true), p);
        const char *name = string(member(x, "name", p, true), p);
        if (!id || !name) return false;
        for (size_t j = 0; j < old + i; j++) {
            if (strcmp(c->items[j].external_id, id) == 0)
                return errorf(CONFIG_ERROR_SCHEMA, p, "duplicate item id");
        }
        ItemDef *q = &c->items[old + i];
        q->id = (ItemId)(old + i);
        q->type = ITEM_PRODUCT;
        q->external_id = copy_string(id);
        q->name = copy_string(name);
        if (q->external_id == NULL || q->name == NULL) return alloc_error();
        v = member(x, "processed_base_price", p, true);
        if (!number(v, p, 0, -1, false)) return false;
        q->base_price = v->valuedouble;
        v = cJSON_GetObjectItem(x, "price_variation");
        q->price_variation = .12;
        if (v != NULL && !number(v, p, 0, 1, false)) return false;
        if (v != NULL) q->price_variation = v->valuedouble;
        if (!parse_seasonal(cJSON_GetObjectItem(x, "seasonal_demand"), p,
                            q->seasonal_demand)) return false;
        i++;
    }

    cJSON *recipes = cJSON_GetObjectItem(root, "recipes");
    if (recipes != NULL && !array(recipes, "processing.recipes")) return false;
    size_t rn = recipes == NULL ? 0 : (size_t)cJSON_GetArraySize(recipes);
    c->recipe_count = rn;
    c->recipes = rn ? calloc(rn, sizeof(*c->recipes)) : NULL;
    if (rn && c->recipes == NULL) return alloc_error();
    static const char *const rf[] = {"id", "input_item_id", "output_item_id", "input_quantity",
                                     "output_quantity", "min_quality", "processing_days", "cost",
                                     "shelf_life_days", NULL};
    size_t r = 0;
    cJSON_ArrayForEach(x, recipes) {
        char p[128];
        snprintf(p, sizeof(p), "processing.recipes[%zu]", r);
        if (!object(x, p) || !only(x, p, rf)) return false;
        const char *id = string(member(x, "id", p, true), p);
        const char *in = string(member(x, "input_item_id", p, true), p);
        const char *out = string(member(x, "output_item_id", p, true), p);
        if (!id || !in || !out) return false;
        for (size_t j = 0; j < r; j++) {
            if (strcmp(c->recipes[j].external_id, id) == 0)
                return errorf(CONFIG_ERROR_SCHEMA, p, "duplicate recipe id");
        }
        int ii = -1, oi = -1;
        for (size_t j = 0; j < c->item_count; j++) {
            if (strcmp(c->items[j].external_id, in) == 0) ii = (int)j;
            if (strcmp(c->items[j].external_id, out) == 0) oi = (int)j;
        }
        if (ii < 0 || oi < 0) return errorf(CONFIG_ERROR_REFERENCE, p, "unknown recipe item");
        RecipeDef *z = &c->recipes[r];
        z->id = (RecipeId)r;
        z->external_id = copy_string(id);
        if (z->external_id == NULL) return alloc_error();
        z->input_item_id = (ItemId)ii;
        z->output_item_id = (ItemId)oi;
        v = member(x, "input_quantity", p, true);
        if (!integer(v, p, 1)) return false;
        z->input_quantity = v->valueint;
        v = member(x, "output_quantity", p, true);
        if (!integer(v, p, 1)) return false;
        z->output_quantity = v->valueint;
        v = member(x, "processing_days", p, true);
        if (!integer(v, p, 1)) return false;
        z->processing_days = v->valueint;
        v = member(x, "cost", p, true);
        if (!number(v, p, 0, -1, false)) return false;
        z->cost = v->valuedouble;
         v = member(x, "shelf_life_days", p, true);
         if (!integer(v, p, 1)) return false;
         z->shelf_life_days = v->valueint;
        v = cJSON_GetObjectItem(x, "min_quality");
        z->min_quality = QUALITY_PROCESSING;
        if (v != NULL) {
            if (!enum_value(v, p, QUALITY_NAMES)) return false;
            z->min_quality = quality(v->valuestring);
        }
        r++;
    }
    return true;
}

/* The remaining section parsers are deliberately table-driven in the public
 * contract: they validate every scalar before copying it and never retain a
 * cJSON pointer. Each takes the single Document it reads from -- the caller
 * (config_load_directory) picks the right docs[] slot, same as
 * parse_soil_dynamics/parse_contract_optional below already do. */
static bool parse_fertilizer(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    c->fertilizer = (FertilizerConfig){0};
    cJSON *r=d->json,*v;
    static const char *const ff[]={"cost","yield_bonus_pct","loss_chance_reduction","quality_bonus","nutrients_added",NULL};
    if(!object(r,"fertilizer")||!only(r,"fertilizer",ff))return false;
    v=member(r,"cost","fertilizer.cost",true);if(!number(v,"fertilizer.cost",0,-1,false))return false;c->fertilizer.cost=v->valuedouble;
    v=member(r,"yield_bonus_pct","fertilizer.yield_bonus_pct",true);if(!number(v,"fertilizer.yield_bonus_pct",0,-1,false))return false;c->fertilizer.yield_bonus_pct=v->valuedouble;
    v=member(r,"loss_chance_reduction","fertilizer.loss_chance_reduction",true);if(!number(v,"fertilizer.loss_chance_reduction",0,1,false))return false;c->fertilizer.loss_chance_reduction=v->valuedouble;
    v=cJSON_GetObjectItem(r,"quality_bonus");if(v&&!number(v,"fertilizer.quality_bonus",0,1,false))return false;c->fertilizer.quality_bonus=v?v->valuedouble:.05;
     if(!parse_nutrients(cJSON_GetObjectItem(r,"nutrients_added"),"fertilizer.nutrients_added",&c->fertilizer.nutrients_added,DEFAULT_FERTILIZER_NUTRIENTS_ADDED,1))return false;
    return true;
}

static bool parse_watering(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    c->watering = (WateringConfig){0};
    cJSON *r=d->json,*v;
    if(!object(r,"watering"))return false;const char*wk[]={"neglect_loss_chance_penalty_per_day","neglect_yield_penalty_per_day","max_neglect_loss_chance_bonus","max_neglect_yield_penalty","cost_per_plot","moisture_added",NULL};if(!only(r,"watering",wk))return false;double*wf[]={&c->watering.neglect_loss_chance_penalty_per_day,&c->watering.neglect_yield_penalty_per_day,&c->watering.max_neglect_loss_chance_bonus,&c->watering.max_neglect_yield_penalty,&c->watering.cost_per_plot,&c->watering.moisture_added};double wmax[]={1,1,1,1,-1,1};for(int i=0;i<6;i++){v=member(r,wk[i],"watering",true);if(!number(v,"watering",0,wmax[i],false))return false;*wf[i]=v->valuedouble;}
    return true;
}

static bool parse_storage(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    c->storage = (StorageConfig){0,100,1};
    cJSON *r=d->json,*v;
    if(!object(r,"storage"))return false;v=member(r,"capacity","storage.capacity",true);if(!integer(v,"storage.capacity",0))return false;c->storage.capacity=v->valueint;v=member(r,"shelf_life_multiplier","storage.shelf_life_multiplier",true);if(!number(v,"storage.shelf_life_multiplier",0,-1,true))return false;c->storage.shelf_life_multiplier=v->valuedouble;v=member(r,"daily_cost","storage.daily_cost",true);if(!number(v,"storage.daily_cost",0,-1,false))return false;c->storage.daily_cost=v->valuedouble;
    return true;
}

static bool parse_contracts_required(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    c->contracts = (ContractsConfig){1.15,.45,3,7,.35,6,5,.25};
    cJSON *r=d->json,*v;
    static const char*const cf[]={"offer_interval_days","default_penalty_rate","production_safety_factor","offer_expiry_days","fallback_price_multiplier","relationship_gain_per_delivery","relationship_loss_per_failure","relationship_bonus_cap",NULL};if(!object(r,"contracts")||!only(r,"contracts",cf))return false;v=member(r,"offer_interval_days","contracts.offer_interval_days",true);if(!integer(v,"contracts.offer_interval_days",1))return false;c->contracts.offer_interval_days=v->valueint;v=member(r,"default_penalty_rate","contracts.default_penalty_rate",true);if(!number(v,"contracts.default_penalty_rate",0,1,false))return false;c->contracts.default_penalty_rate=v->valuedouble;v=member(r,"production_safety_factor","contracts.production_safety_factor",true);if(!number(v,"contracts.production_safety_factor",0,1,false))return false;c->contracts.production_safety_factor=v->valuedouble;
    return true;
}

static bool parse_soil_initial(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *r = d->json;
    cJSON *v;
    static const char *const sf[] = {"initial", "regen_per_day", "dynamics", NULL};
    if (!object(r, "soil") || !only(r, "soil", sf)) return false;

    c->soil_initial = (SoilInitial){0.65, 0.75, 0.75, 0.75, 6.5, 0.7, 0.05, 0.03};
    cJSON *initial = cJSON_GetObjectItem(r, "initial");
    if (initial == NULL) return true;
    if (!object(initial, "soil.initial")) return false;
    static const char *const sk[] = {"moisture", "nitrogen", "phosphorus", "potassium",
                                     "ph", "soil_health", "pest_pressure", "disease_pressure", NULL};
    if (!only(initial, "soil.initial", sk)) return false;
    double *fields[] = {&c->soil_initial.moisture, &c->soil_initial.nitrogen,
                        &c->soil_initial.phosphorus, &c->soil_initial.potassium,
                        &c->soil_initial.ph, &c->soil_initial.soil_health,
                        &c->soil_initial.pest_pressure, &c->soil_initial.disease_pressure};
    for (size_t i = 0; sk[i] != NULL; i++) {
        v = cJSON_GetObjectItem(initial, sk[i]);
        if (v == NULL) continue;
        if (!number(v, "soil.initial", i == 4 ? 0 : 0, i == 4 ? 14 : 1, false))
            return false;
        *fields[i] = v->valuedouble;
    }
    return true;
}

static bool parse_weather(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *r=d->json,*v;
    static const char*const ww[]={"season_length_days","seasons",NULL};if(!object(r,"weather")||!only(r,"weather",ww))return false;v=member(r,"season_length_days","weather.season_length_days",true);if(!integer(v,"weather.season_length_days",1))return false;c->weather.season_length_days=v->valueint;v=member(r,"seasons","weather.seasons",true);if(!object(v,"weather.seasons"))return false;static const char*const sn[]={"spring","summer","autumn","winter",NULL};if(!only(v,"weather.seasons",sn))return false;for(int s=0;s<4;s++){cJSON*q=member(v,sn[s],"weather.seasons",true);if(!object(q,"weather.season"))return false;cJSON*t=member(q,"temperature_range","weather.season.temperature_range",true);if(!range2(t,"weather.temperature_range",false,-DBL_MAX,DBL_MAX))return false;c->weather.by_season[s].temperature_low=cJSON_GetArrayItem(t,0)->valuedouble;c->weather.by_season[s].temperature_high=cJSON_GetArrayItem(t,1)->valuedouble;double*wf2[]={&c->weather.by_season[s].rain_chance,&c->weather.by_season[s].evaporation};const char*wk2[]={"rain_chance","evaporation"};for(int j=0;j<2;j++){t=member(q,wk2[j],"weather.season",true);if(!number(t,"weather.season",0,1,false))return false;*wf2[j]=t->valuedouble;}t=member(q,"rainfall_range","weather.season",true);if(!range2(t,"weather.rainfall_range",false,0,1))return false;c->weather.by_season[s].rainfall_low=cJSON_GetArrayItem(t,0)->valuedouble;c->weather.by_season[s].rainfall_high=cJSON_GetArrayItem(t,1)->valuedouble;}
    return true;
}

static bool parse_soil_dynamics(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *root = d->json;
    cJSON *regen = cJSON_GetObjectItem(root, "regen_per_day");
    cJSON *dynamics = cJSON_GetObjectItem(root, "dynamics");
    static const char *const regen_keys[] = {
        "moisture", "nitrogen", "phosphorus", "potassium", "soil_health",
        "pest_pressure", "disease_pressure", NULL};
    static const char *const dynamics_keys[] = {
        "harvest_soil_health_cost", "min_soil_health", "fallow_pest_decay",
        "fallow_disease_decay", "fallow_soil_health_regen", "pest_growth_per_day",
        "disease_growth_per_rainfall", "max_pest_pressure", "max_disease_pressure",
        "same_family_yield_penalty", "same_family_quality_penalty",
        "soil_health_yield_floor", "soil_health_yield_span", NULL};
    if (regen != NULL) {
        if (!object(regen, "soil.regen_per_day") || !only(regen, "soil.regen_per_day", regen_keys)) return false;
        double *fields[] = {&c->plot_regen.moisture, &c->plot_regen.nitrogen,
            &c->plot_regen.phosphorus, &c->plot_regen.potassium,
            &c->plot_regen.soil_health, &c->plot_regen.pest_pressure,
            &c->plot_regen.disease_pressure};
        for (size_t i = 0; regen_keys[i] != NULL; i++) {
            cJSON *v = cJSON_GetObjectItem(regen, regen_keys[i]);
            if (v != NULL && !number(v, "soil.regen_per_day", 0, 1, false)) return false;
            if (v != NULL) *fields[i] = v->valuedouble;
        }
    }
    if (dynamics != NULL) {
        if (!object(dynamics, "soil.dynamics") || !only(dynamics, "soil.dynamics", dynamics_keys)) return false;
        double *fields[] = {&c->soil_dynamics.harvest_soil_health_cost,
            &c->soil_dynamics.min_soil_health, &c->soil_dynamics.fallow_pest_decay,
            &c->soil_dynamics.fallow_disease_decay, &c->soil_dynamics.fallow_soil_health_regen,
            &c->soil_dynamics.pest_growth_per_day, &c->soil_dynamics.disease_growth_per_rainfall,
            &c->soil_dynamics.max_pest_pressure, &c->soil_dynamics.max_disease_pressure,
            &c->soil_dynamics.same_family_yield_penalty, &c->soil_dynamics.same_family_quality_penalty,
            &c->soil_dynamics.soil_health_yield_floor, &c->soil_dynamics.soil_health_yield_span};
        const double defaults[] = {0.02, 0.1, 0.9, 0.9, 0.005, 0.005, 0.08, 0.8, 0.8, 0.85, 0.9, 0.85, 0.25};
        for (size_t i = 0; dynamics_keys[i] != NULL; i++) {
            *fields[i] = defaults[i];
            cJSON *v = cJSON_GetObjectItem(dynamics, dynamics_keys[i]);
            double maximum = i >= 11 ? 2.0 : (i == 6 ? -1.0 : 1.0);
            if (v != NULL && !number(v, "soil.dynamics", 0, maximum, false)) return false;
            if (v != NULL) *fields[i] = v->valuedouble;
        }
    } else {
        c->soil_dynamics = (SoilDynamics){0.02, 0.1, 0.9, 0.9, 0.005, 0.005,
            0.08, 0.8, 0.8, 0.85, 0.9, 0.85, 0.25};
    }
    return true;
}

static bool parse_contract_optional(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *root = d->json;
    const char *keys[] = {"offer_expiry_days", "fallback_price_multiplier",
        "relationship_gain_per_delivery", "relationship_loss_per_failure", "relationship_bonus_cap"};
    cJSON *v = cJSON_GetObjectItem(root, keys[0]);
    if (v != NULL && (!integer(v, "contracts.offer_expiry_days", 1))) return false;
    if (v != NULL) c->contracts.offer_expiry_days = v->valueint;
    double *fields[] = {&c->contracts.fallback_price_multiplier,
        &c->contracts.relationship_gain_per_delivery, &c->contracts.relationship_loss_per_failure,
        &c->contracts.relationship_bonus_cap};
    const double maximums[] = {-1, -1, -1, -1};
    for (size_t i = 1; i < 5; i++) {
        v = cJSON_GetObjectItem(root, keys[i]);
        if (v != NULL && !number(v, "contracts", 0, maximums[i - 1], false)) return false;
        if (v != NULL) fields[i - 1][0] = v->valuedouble;
    }
    return true;
}

static bool parse_markets(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *r=d->json;if(!object(r,"markets"))return false;static const char *const top[]={"default_variation","minimum_supply_multiplier","supply_decay","channels",NULL};if(!only(r,"markets",top))return false; cJSON *v=member(r,"default_variation","markets.default_variation",true);if(!number(v,"markets.default_variation",0,1,false))return false;v=member(r,"minimum_supply_multiplier","markets.minimum_supply_multiplier",true);if(!number(v,"markets.minimum_supply_multiplier",0,1,false))return false;c->markets.minimum_supply_multiplier=v->valuedouble;v=member(r,"supply_decay","markets.supply_decay",true);if(!number(v,"markets.supply_decay",0,1,false))return false;c->markets.supply_decay=v->valuedouble;cJSON *a=member(r,"channels","markets.channels",true);if(!array(a,"markets.channels"))return false;size_t n=(size_t)cJSON_GetArraySize(a);c->channel_count=n;c->channels=n?calloc(n,sizeof(*c->channels)):NULL;if(n&&!c->channels)return alloc_error();static const char *const fields[]={"id","name","price_multiplier","min_quality","daily_capacity","fee_rate","flat_fee","min_reputation","reputation_bonus",NULL};cJSON *x;size_t i=0;c->spot_channel_id=INVALID_ID;cJSON_ArrayForEach(x,a){char p[128];snprintf(p,sizeof(p),"markets.channels[%zu]",i);if(!object(x,p)||!only(x,p,fields))return false;const char *id=string(member(x,"id",p,true),p);if(!id)return false;for(size_t j=0;j<i;j++)if(strcmp(c->channels[j].external_id,id)==0)return errorf(CONFIG_ERROR_SCHEMA,p,"duplicate channel id");ChannelDef*q=&c->channels[i];q->channel_id=(ChannelId)i;q->external_id=copy_string(id);if(!q->external_id)return alloc_error();if(strcmp(id,"spot")==0)c->spot_channel_id=q->channel_id;v=member(x,"price_multiplier",p,true);if(!number(v,p,0,-1,false))return false;q->price_multiplier=v->valuedouble;v=cJSON_GetObjectItem(x,"min_quality");q->min_quality_rank=QUALITY_REJECTED;if(v){if(!enum_value(v,p,QUALITY_NAMES))return false;q->min_quality_rank=quality(v->valuestring);}v=member(x,"daily_capacity",p,true);if(!integer(v,p,1))return false;q->daily_capacity=v->valueint;q->has_daily_capacity=true;v=cJSON_GetObjectItem(x,"fee_rate");if(v&&!number(v,p,0,1,false))return false;if(v)q->fee_rate=v->valuedouble;v=cJSON_GetObjectItem(x,"flat_fee");if(v&&!number(v,p,0,-1,false))return false;if(v)q->flat_fee=v->valuedouble;v=cJSON_GetObjectItem(x,"min_reputation");if(v&&!number(v,p,0,-1,false))return false;if(v)q->min_reputation=v->valuedouble;v=cJSON_GetObjectItem(x,"reputation_bonus");if(v&&!number(v,p,0,-1,false))return false;if(v)q->reputation_bonus=v->valuedouble;i++;}if(!id_valid(c->spot_channel_id))return errorf(CONFIG_ERROR_REFERENCE,"markets.channels","spot channel is required");return true;
}

static bool parse_buyers(Document *d, ResolvedConfig *c) {
    g_file = d->name;
    cJSON *root = d->json;
    if (!array(root, "buyers")) return false;
    size_t n = (size_t)cJSON_GetArraySize(root);
    c->buyer_count = n;
    c->buyers = n ? calloc(n, sizeof(*c->buyers)) : NULL;
    if (n && c->buyers == NULL) return alloc_error();
    static const char *const fields[] = {"id", "name", "items", "quantity_range", "min_quality",
                                         "contract_price_multiplier", "deadline_days", "penalty_rate",
                                         "min_reputation", "relationship_bonus_rate", NULL};
    cJSON *x;
    size_t i = 0;
    cJSON_ArrayForEach(x, root) {
        char p[128];
        snprintf(p, sizeof(p), "buyers[%zu]", i);
        if (!object(x, p) || !only(x, p, fields)) return false;
        const char *id = string(member(x, "id", p, true), p);
        if (id == NULL) return false;
        cJSON *name_value = cJSON_GetObjectItem(x, "name");
        const char *name = id;
        if (name_value != NULL && !cJSON_IsNull(name_value)) {
            name = string(name_value, p);
            if (name == NULL) return false;
        }
        for (size_t j = 0; j < i; j++) {
            if (strcmp(c->buyers[j].external_id, id) == 0)
                return errorf(CONFIG_ERROR_SCHEMA, p, "duplicate buyer id");
        }
        BuyerDef *buyer = &c->buyers[i];
        buyer->id = (BuyerId)i;
        buyer->external_id = copy_string(id);
        buyer->name = copy_string(name);
        if (buyer->external_id == NULL || buyer->name == NULL) return alloc_error();
        cJSON *items = member(x, "items", p, true);
        if (!array(items, p)) return false;
        size_t item_count = (size_t)cJSON_GetArraySize(items);
        buyer->allowed_items = item_count ? calloc(item_count, sizeof(ItemId)) : NULL;
        buyer->allowed_item_count = item_count;
        if (item_count && buyer->allowed_items == NULL) return alloc_error();
        cJSON *item;
        size_t k = 0;
        cJSON_ArrayForEach(item, items) {
            char q[128];
            snprintf(q, sizeof(q), "%s.items[%zu]", p, k);
            const char *item_id = string(item, q);
            if (item_id == NULL) return false;
            int found = -1;
            for (size_t j = 0; j < c->item_count; j++) {
                if (strcmp(c->items[j].external_id, item_id) == 0) found = (int)j;
            }
            if (found < 0) return errorf(CONFIG_ERROR_REFERENCE, q, "unknown item");
            buyer->allowed_items[k++] = (ItemId)found;
        }
        items = member(x, "quantity_range", p, true);
        if (!range2(items, p, true, 1, -1)) return false;
        buyer->quantity_min = cJSON_GetArrayItem(items, 0)->valueint;
        buyer->quantity_max = cJSON_GetArrayItem(items, 1)->valueint;
        cJSON *v = cJSON_GetObjectItem(x, "min_quality");
        buyer->min_quality = QUALITY_STANDARD;
        if (v != NULL) {
            if (!enum_value(v, p, QUALITY_NAMES)) return false;
            buyer->min_quality = quality(v->valuestring);
        }
        v = member(x, "deadline_days", p, true);
        if (!integer(v, p, 1)) return false;
        buyer->deadline_days = v->valueint;
        v = cJSON_GetObjectItem(x, "contract_price_multiplier");
        buyer->contract_price_multiplier = 1.2;
        if (v != NULL && !number(v, p, 0, -1, false)) return false;
        if (v != NULL) buyer->contract_price_multiplier = v->valuedouble;
        v = cJSON_GetObjectItem(x, "penalty_rate");
        buyer->penalty_rate = c->contracts.default_penalty_rate;
        if (v != NULL && !number(v, p, 0, 1, false)) return false;
        if (v != NULL) buyer->penalty_rate = v->valuedouble;
        v = cJSON_GetObjectItem(x, "min_reputation");
        buyer->min_reputation = 0;
        if (v != NULL && !number(v, p, 0, -1, false)) return false;
        if (v != NULL) buyer->min_reputation = v->valuedouble;
        v = cJSON_GetObjectItem(x, "relationship_bonus_rate");
        buyer->relationship_bonus_rate = 0;
        if (v != NULL && !number(v, p, 0, -1, false)) return false;
        if (v != NULL) buyer->relationship_bonus_rate = v->valuedouble;
        i++;
    }
    return true;
}

static void apply_resolved_defaults(Document *processing, Document *markets, ResolvedConfig *c) {
    cJSON *products = cJSON_GetObjectItem(processing->json, "products");
    for (size_t i = c->crop_count; i < c->item_count; i++) {
        cJSON *product = cJSON_GetArrayItem(products, (int)(i - c->crop_count));
        if (cJSON_GetObjectItem(product, "price_variation") == NULL)
            c->items[i].price_variation = cJSON_GetObjectItem(markets->json, "default_variation")->valuedouble;
    }
    cJSON *channels = cJSON_GetObjectItem(markets->json, "channels");
    for (size_t i = 0; i < c->channel_count; i++) {
        cJSON *channel = cJSON_GetArrayItem(channels, (int)i);
        if (cJSON_GetObjectItem(channel, "reputation_bonus") == NULL)
            c->channels[i].reputation_bonus = 0.002;
    }
}

static bool validate_unlocks(Document *d, ResolvedConfig *c) {
    g_file=d->name; cJSON*x;size_t i=0;static const char*const uf[]={"type","value","id",NULL};cJSON_ArrayForEach(x,d->json){char p[128];snprintf(p,sizeof(p),"crops[%zu].unlock_requirement",i++);cJSON*u=cJSON_GetObjectItem(x,"unlock_requirement");if(u==NULL||cJSON_IsNull(u)){c->crops[i-1].unlock_requirement.type=UNLOCK_NONE;continue;}if(!object(u,p)||!only(u,p,uf))return false;cJSON*v=member(u,"type",p,true);if(!enum_value(v,p,(const char*[]){"total_revenue","upgrade",NULL}))return false;if(strcmp(v->valuestring,"total_revenue")==0){v=member(u,"value",p,true);if(!number(v,p,0,-1,false))return false;c->crops[i-1].unlock_requirement.type=UNLOCK_REVENUE;c->crops[i-1].unlock_requirement.revenue_threshold=v->valuedouble;}else{v=member(u,"id",p,true);const char*s=string(v,p);if(!s)return false;int found=-1;for(size_t j=0;j<c->upgrade_count;j++)if(strcmp(c->upgrades[j].external_id,s)==0)found=(int)j;if(found<0)return errorf(CONFIG_ERROR_REFERENCE,p,"unknown upgrade");c->crops[i-1].unlock_requirement.type=UNLOCK_UPGRADE;c->crops[i-1].unlock_requirement.upgrade=(UpgradeId)found;}}return true;
}

static void destroy_partial(ResolvedConfig *c) {
    if (c == NULL) return;
    if (c->items != NULL)
        for (size_t i=0;i<c->item_count;i++){free((char*)c->items[i].external_id);free((char*)c->items[i].name);}
    if (c->crops != NULL)
        for (size_t i=0;i<c->crop_count;i++) free((char*)c->crops[i].family);
    if (c->upgrades != NULL)
        for (size_t i=0;i<c->upgrade_count;i++){free((char*)c->upgrades[i].external_id);free((char*)c->upgrades[i].name);}
    if (c->recipes != NULL)
        for (size_t i=0;i<c->recipe_count;i++)free((char*)c->recipes[i].external_id);
    if (c->channels != NULL)
        for (size_t i=0;i<c->channel_count;i++)free((char*)c->channels[i].external_id);
    if (c->buyers != NULL)
        for(size_t i=0;i<c->buyer_count;i++){free((char*)c->buyers[i].external_id);free((char*)c->buyers[i].name);free(c->buyers[i].allowed_items);}
    free(c->items);free(c->crops);free(c->upgrades);free(c->recipes);free(c->channels);free(c->buyers);memset(c,0,sizeof(*c));
}

void config_destroy(ResolvedConfig *config) { destroy_partial(config); }

/* config.c's config_find_* accessors index these arrays directly by id
 * rather than scanning for a match, which is only correct while every id
 * equals its own array position. Each parser above assigns ids that way, so
 * this is a postcondition check, not a parse step -- one pass at load time
 * in exchange for not re-verifying it on every lookup. Without it the
 * invariant would be enforced by source comments alone, and a config whose
 * ids drifted would silently resolve to the *wrong* record rather than
 * fail. */
static bool validate_ids_are_indexes(const ResolvedConfig *c) {
    for(size_t i=0;i<c->item_count;i++)if(c->items[i].id!=(ItemId)i)return errorf(CONFIG_ERROR_SCHEMA,"items","item id must equal its index");
    for(size_t i=0;i<c->crop_count;i++)if(c->crops[i].item_id!=(ItemId)i)return errorf(CONFIG_ERROR_SCHEMA,"crops","crop item id must equal its index");
    for(size_t i=0;i<c->upgrade_count;i++)if(c->upgrades[i].id!=(UpgradeId)i)return errorf(CONFIG_ERROR_SCHEMA,"upgrades","upgrade id must equal its index");
    for(size_t i=0;i<c->recipe_count;i++)if(c->recipes[i].id!=(RecipeId)i)return errorf(CONFIG_ERROR_SCHEMA,"processing.recipes","recipe id must equal its index");
    for(size_t i=0;i<c->channel_count;i++)if(c->channels[i].channel_id!=(ChannelId)i)return errorf(CONFIG_ERROR_SCHEMA,"markets.channels","channel id must equal its index");
    for(size_t i=0;i<c->buyer_count;i++)if(c->buyers[i].id!=(BuyerId)i)return errorf(CONFIG_ERROR_SCHEMA,"buyers","buyer id must equal its index");
    return true;
}

bool config_load_directory(const char *directory, ResolvedConfig *out, ConfigError *error) {
    static const char *const names[] = {"crops","upgrades","fertilizer","watering_settings","soil","weather","markets","contracts","buyers","processing","storage"};
    if (out == NULL || directory == NULL) { if(error){error->code=CONFIG_ERROR_ARGUMENT;snprintf(error->message,sizeof(error->message),"invalid argument");} return false; }
    memset(out,0,sizeof(*out)); g_error=error; if(error)memset(error,0,sizeof(*error));
    Document docs[11]={0};
    for(int i=0;i<11;i++)if(!read_document(directory,names[i],&docs[i]))goto fail;
    /* Full schema resolution is intentionally staged here; all allocation and
     * ownership is local until the final success return. */
    if (!parse_world(&docs[0],out) || !parse_upgrades(&docs[1],out) ||
        !validate_unlocks(&docs[0],out) ||
         !parse_fertilizer(&docs[2],out) || !parse_watering(&docs[3],out) ||
         !parse_storage(&docs[10],out) || !parse_contracts_required(&docs[7],out) ||
         !parse_soil_initial(&docs[4],out) || !parse_weather(&docs[5],out) ||
         !parse_soil_dynamics(&docs[4],out) ||
         !parse_contract_optional(&docs[7],out) || !parse_processing(&docs[9],out) ||
         !parse_markets(&docs[6],out) || !parse_buyers(&docs[8],out)) goto fail;
    if (out->spot_channel_id == INVALID_ID) {
        g_file = docs[6].name;
        errorf(CONFIG_ERROR_SCHEMA, "markets.channels", "a 'spot' channel is required");
        goto fail;
    }
    apply_resolved_defaults(&docs[9], &docs[6], out);
    if(!validate_ids_are_indexes(out))goto fail;
    /* cJSON trees are kept until every reference has been resolved. */
    for(int i=0;i<11;i++)cJSON_Delete(docs[i].json);
    return true;
fail:
    for(int i=0;i<11;i++)cJSON_Delete(docs[i].json);
    destroy_partial(out); return false;
}

bool config_load_simulation_settings(const char *directory, SimulationSettings *out, ConfigError *error) {
    if(out==NULL||directory==NULL){if(error){error->code=CONFIG_ERROR_ARGUMENT;snprintf(error->message,sizeof(error->message),"invalid argument");}return false;}
    memset(out,0,sizeof(*out));g_error=error;if(error)memset(error,0,sizeof(*error));Document d={0};if(!read_document(directory,"simulation_settings",&d))return false;
    if(!object(d.json,"simulation_settings"))goto fail;
     cJSON *v=member(d.json,"start_money","simulation_settings.start_money",true);if(!number(v,"simulation_settings.start_money",0,-1,false))goto fail;out->start_money=v->valuedouble;
    v=member(d.json,"start_slots","simulation_settings.start_slots",true);if(!integer(v,"simulation_settings.start_slots",1))goto fail;out->start_slots=v->valueint;
    v=member(d.json,"days","simulation_settings.days",true);if(!integer(v,"simulation_settings.days",1))goto fail;out->days=v->valueint;
    v=member(d.json,"operating_reserve","simulation_settings.operating_reserve",false);if(v&&!number(v,"simulation_settings.operating_reserve",0,-1,false))goto fail;out->operating_reserve=v?v->valuedouble:0;
     v=member(d.json,"seed","simulation_settings.seed",false);if(v&&!cJSON_IsNull(v)){if(!seed_integer(v,"simulation_settings.seed",&out->seed))goto fail;out->has_seed=true;}
    cJSON_Delete(d.json);return true;
fail:cJSON_Delete(d.json);memset(out,0,sizeof(*out));return false;
}
