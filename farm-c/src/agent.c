#include "agent.h"

#include <stdlib.h>
#include <string.h>

#include "vec_util.h"

bool contract_decision_push(ContractDecisionBuffer *buffer, ContractId contract_id) {
    if (!vec_grow((void **)&buffer->data, &buffer->capacity, buffer->count, sizeof(ContractId))) {
        return false;
    }
    buffer->data[buffer->count++] = contract_id;
    return true;
}

void contract_decision_free(ContractDecisionBuffer *buffer) {
    free(buffer->data);
    *buffer = (ContractDecisionBuffer){0};
}

bool delivery_decision_push(DeliveryDecisionBuffer *buffer, DeliveryDecision decision) {
    if (!vec_grow((void **)&buffer->data, &buffer->capacity, buffer->count,
                   sizeof(DeliveryDecision))) {
        return false;
    }
    buffer->data[buffer->count++] = decision;
    return true;
}

void delivery_decision_free(DeliveryDecisionBuffer *buffer) {
    free(buffer->data);
    *buffer = (DeliveryDecisionBuffer){0};
}

bool processing_decision_push(ProcessingDecisionBuffer *buffer, ProcessingDecision decision) {
    if (!vec_grow((void **)&buffer->data, &buffer->capacity, buffer->count,
                   sizeof(ProcessingDecision))) {
        return false;
    }
    buffer->data[buffer->count++] = decision;
    return true;
}

void processing_decision_free(ProcessingDecisionBuffer *buffer) {
    free(buffer->data);
    *buffer = (ProcessingDecisionBuffer){0};
}

bool sale_decision_push(SalesDecisionBuffer *buffer, SaleDecision decision) {
    if (!vec_grow((void **)&buffer->data, &buffer->capacity, buffer->count,
                   sizeof(SaleDecision))) {
        return false;
    }
    buffer->data[buffer->count++] = decision;
    return true;
}

void sales_decision_free(SalesDecisionBuffer *buffer) {
    free(buffer->data);
    *buffer = (SalesDecisionBuffer){0};
}

const Agent *agent_registry_find(const char *strategy_name) {
    for (const AgentRegistryEntry *entry = AGENT_REGISTRY; entry->strategy_name != NULL;
         entry++) {
        if (strcmp(entry->strategy_name, strategy_name) == 0) {
            return entry->agent;
        }
    }
    return NULL;
}
