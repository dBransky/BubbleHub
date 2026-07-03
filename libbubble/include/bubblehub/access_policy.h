#pragma once

#include <stddef.h>

#define BUBBLEHUB_ACCESS_AGENT_ID_SIZE 64U
#define BUBBLEHUB_ACCESS_KIND_SIZE 32U
#define BUBBLEHUB_ACCESS_SUBJECT_SIZE 256U
#define BUBBLEHUB_ACCESS_METHOD_SIZE 64U
#define BUBBLEHUB_ACCESS_PATH_SIZE 512U
#define BUBBLEHUB_ACCESS_POLICY_SIZE 16U
#define BUBBLEHUB_ACCESS_ID_SIZE 64U

typedef enum {
    BUBBLEHUB_ACCESS_DECISION_DENY = 0,
    BUBBLEHUB_ACCESS_DECISION_APPROVE = 1,
} bubblehub_access_decision;

typedef struct {
    char kind[BUBBLEHUB_ACCESS_KIND_SIZE];
    char subject[BUBBLEHUB_ACCESS_SUBJECT_SIZE];
    char method[BUBBLEHUB_ACCESS_METHOD_SIZE];
    char path[BUBBLEHUB_ACCESS_PATH_SIZE];
} bubblehub_access_request;

int bubblehub_access_manifest_path(const char *agent_id, char *buffer, size_t buffer_size);
int bubblehub_access_evaluate(
    const char *agent_id,
    const bubblehub_access_request *request,
    int allow_prompt,
    bubblehub_access_decision *decision);
int bubblehub_access_needs_prompt(
    const char *agent_id,
    const bubblehub_access_request *request,
    int *needs_prompt);
int bubblehub_access_apply_policy(
    const char *agent_id,
    const bubblehub_access_request *request,
    const char *policy);
char *bubblehub_access_manifest_json(const char *agent_id);
char *bubblehub_access_pending_json(void);
void bubblehub_access_free_string(char *value);
