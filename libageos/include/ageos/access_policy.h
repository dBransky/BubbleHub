#pragma once

#include <stddef.h>

#define AGEOS_ACCESS_AGENT_ID_SIZE 64U
#define AGEOS_ACCESS_KIND_SIZE 32U
#define AGEOS_ACCESS_SUBJECT_SIZE 256U
#define AGEOS_ACCESS_METHOD_SIZE 64U
#define AGEOS_ACCESS_PATH_SIZE 512U
#define AGEOS_ACCESS_POLICY_SIZE 16U
#define AGEOS_ACCESS_ID_SIZE 64U

typedef enum {
    AGEOS_ACCESS_DECISION_DENY = 0,
    AGEOS_ACCESS_DECISION_APPROVE = 1,
} ageos_access_decision;

typedef struct {
    char kind[AGEOS_ACCESS_KIND_SIZE];
    char subject[AGEOS_ACCESS_SUBJECT_SIZE];
    char method[AGEOS_ACCESS_METHOD_SIZE];
    char path[AGEOS_ACCESS_PATH_SIZE];
} ageos_access_request;

int ageos_access_manifest_path(const char *agent_id, char *buffer, size_t buffer_size);
int ageos_access_evaluate(
    const char *agent_id,
    const ageos_access_request *request,
    int allow_prompt,
    ageos_access_decision *decision);
int ageos_access_needs_prompt(
    const char *agent_id,
    const ageos_access_request *request,
    int *needs_prompt);
int ageos_access_apply_policy(
    const char *agent_id,
    const ageos_access_request *request,
    const char *policy);
char *ageos_access_manifest_json(const char *agent_id);
char *ageos_access_pending_json(void);
void ageos_access_free_string(char *value);
