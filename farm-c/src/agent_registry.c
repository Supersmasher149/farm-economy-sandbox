/* C analogue of main.py's AGENT_REGISTRY, in the same "port the roster in
 * this order" sequence as docs/c-port-plan.md:648-660.
 */
#include "agent.h"

/* Order matches main.py's AGENT_REGISTRY dict exactly (not the sequence
 * docs/c-port-plan.md ported the strategies in) -- main.c's default `batch`
 * (no --strategy given) walks this array to build the agents/names arrays
 * batch_run mints per-strategy seeds from, agent-major and in array order.
 * A reordering here silently changes which seed lands on which strategy for
 * every unseeded default batch, even though `--strategy` explicitly given
 * still resolves by name via agent_registry_find and is unaffected.
 */
const AgentRegistryEntry AGENT_REGISTRY[] = {
    {"fast_seller", &AGENT_FAST_SELLER},
    {"profit_optimizer", &AGENT_PROFIT_OPTIMIZER},
    {"progression_player", &AGENT_PROGRESSION_PLAYER},
    {"neglectful_grower", &AGENT_NEGLECTFUL_GROWER},
    {"reckless_spender", &AGENT_RECKLESS_SPENDER},
    {"random_agent", &AGENT_RANDOM_AGENT},
    {"no_upgrade_player", &AGENT_NO_UPGRADE_PLAYER},
    {"fertilizer_maximalist", &AGENT_FERTILIZER_MAXIMALIST},
    {"diversifier", &AGENT_DIVERSIFIER},
    {"risk_averse_grower", &AGENT_RISK_AVERSE_GROWER},
    {"upgrade_rusher", &AGENT_UPGRADE_RUSHER},
    {NULL, NULL},
};
