/* C analogue of main.py's AGENT_REGISTRY, in the same "port the roster in
 * this order" sequence as docs/c-port-plan.md:648-660.
 */
#include "agent.h"

const AgentRegistryEntry AGENT_REGISTRY[] = {
    {"fast_seller", &AGENT_FAST_SELLER},
    {"no_upgrade_player", &AGENT_NO_UPGRADE_PLAYER},
    {"neglectful_grower", &AGENT_NEGLECTFUL_GROWER},
    {"reckless_spender", &AGENT_RECKLESS_SPENDER},
    {"risk_averse_grower", &AGENT_RISK_AVERSE_GROWER},
    {"diversifier", &AGENT_DIVERSIFIER},
    {"upgrade_rusher", &AGENT_UPGRADE_RUSHER},
    {"progression_player", &AGENT_PROGRESSION_PLAYER},
    {"profit_optimizer", &AGENT_PROFIT_OPTIMIZER},
    {"fertilizer_maximalist", &AGENT_FERTILIZER_MAXIMALIST},
    {"random_agent", &AGENT_RANDOM_AGENT},
    {NULL, NULL},
};
