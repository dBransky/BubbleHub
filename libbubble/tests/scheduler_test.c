#include "bubblehub/scheduler.h"

#include "test_common.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static char g_scheduler_state_path[512];

static int setup_scheduler_state(void) {
    char *dir = test_mkdtemp_copy("bubblehub-scheduler-test");
    TEST_CHECK(dir != NULL);
    snprintf(g_scheduler_state_path, sizeof(g_scheduler_state_path), "%s/scheduler.state", dir);
    setenv("BUBBLEHUB_SCHEDULER_STATE", g_scheduler_state_path, 1);
    return 0;
}

static int test_configure_limits(void) {
    TEST_CHECK(bubblehub_scheduler_configure_limits(16.0, 8.0) == 0);
    return 0;
}

static int test_register_and_snapshot(void) {
    TEST_CHECK(bubblehub_scheduler_register_agent("agt-sched-test", 4242, "/usr/bin/test-agent", 0, "general") == 0);

    char *snapshot = bubblehub_scheduler_snapshot_json();
    TEST_CHECK(snapshot != NULL);
    TEST_CHECK_CONTAINS(snapshot, "\"agent_id\":\"agt-sched-test\"");
    TEST_CHECK_CONTAINS(snapshot, "\"pid\":4242");
    TEST_CHECK_CONTAINS(snapshot, "\"hardware\"");
    bubblehub_scheduler_free_string(snapshot);
    return 0;
}

static int test_deregister_agent(void) {
    TEST_CHECK(bubblehub_scheduler_deregister_agent("agt-sched-test") == 0);

    char *snapshot = bubblehub_scheduler_snapshot_json();
    TEST_CHECK(snapshot != NULL);
    TEST_CHECK(strstr(snapshot, "\"agent_id\":\"agt-sched-test\"") == NULL);
    bubblehub_scheduler_free_string(snapshot);
    return 0;
}

static int test_model_lifecycle(void) {
    TEST_CHECK(
        bubblehub_scheduler_mark_model_loaded("test-model", "general", "llama", 4.0, 2.0, 9001, 8080) == 0);

    char *snapshot = bubblehub_scheduler_snapshot_json();
    TEST_CHECK(snapshot != NULL);
    TEST_CHECK_CONTAINS(snapshot, "\"name\":\"test-model\"");
    TEST_CHECK_CONTAINS(snapshot, "\"port\":8080");
    bubblehub_scheduler_free_string(snapshot);

    TEST_CHECK(bubblehub_scheduler_mark_model_unloaded("test-model") == 0);
    TEST_CHECK(bubblehub_scheduler_evict_model("test-model") == 0);

    snapshot = bubblehub_scheduler_snapshot_json();
    TEST_CHECK(snapshot != NULL);
    TEST_CHECK(strstr(snapshot, "\"name\":\"test-model\"") == NULL);
    bubblehub_scheduler_free_string(snapshot);
    return 0;
}

static int test_queue_item(void) {
    TEST_CHECK(
        bubblehub_scheduler_add_queue_item("job-1", "model_load", "general", "queued-model", 5, "waiting for RAM") == 0);

    char *snapshot = bubblehub_scheduler_snapshot_json();
    TEST_CHECK(snapshot != NULL);
    TEST_CHECK_CONTAINS(snapshot, "\"job_id\":\"job-1\"");
    TEST_CHECK_CONTAINS(snapshot, "\"model_name\":\"queued-model\"");
    bubblehub_scheduler_free_string(snapshot);
    return 0;
}

static int test_admit_model_job(void) {
    int allowed = 0;
    char state[64];
    char reason[256];
    TEST_CHECK(
        bubblehub_scheduler_admit_model_job("general", "new-model", 0, 1.0, 0.0, &allowed, state, sizeof(state), reason, sizeof(reason)) ==
        0);
    TEST_CHECK(allowed == 1);
    return 0;
}

static int test_admit_existing_model(void) {
    TEST_CHECK(
        bubblehub_scheduler_mark_model_loaded("existing-model", "general", "llama", 1.0, 0.0, 9002, 8081) == 0);

    int allowed = 0;
    char state[64];
    char reason[256];
    TEST_CHECK(
        bubblehub_scheduler_admit_model_job(
            "general", "existing-model", 0, 1.0, 0.0, &allowed, state, sizeof(state), reason, sizeof(reason)) == 0);
    TEST_CHECK(allowed == 1);
    TEST_CHECK(bubblehub_scheduler_evict_model("existing-model") == 0);
    return 0;
}

static int test_inference_chat_invalid_request(void) {
    char *response = bubblehub_inference_chat_json("not-json");
    TEST_CHECK(response != NULL);
    TEST_CHECK_CONTAINS(response, "\"error\"");
    bubblehub_scheduler_free_string(response);
    return 0;
}

static int test_invalid_agent_registration(void) {
    TEST_CHECK(bubblehub_scheduler_register_agent("", 1, "/bin/true", 0, "general") == -1);
    TEST_CHECK(bubblehub_scheduler_register_agent(NULL, 1, "/bin/true", 0, "general") == -1);
    return 0;
}

int main(void) {
    if (setup_scheduler_state() != 0) {
        return 1;
    }

    int rc = 0;
    rc |= test_configure_limits();
    rc |= test_register_and_snapshot();
    rc |= test_deregister_agent();
    rc |= test_model_lifecycle();
    rc |= test_queue_item();
    rc |= test_admit_model_job();
    rc |= test_admit_existing_model();
    rc |= test_inference_chat_invalid_request();
    rc |= test_invalid_agent_registration();

    unsetenv("BUBBLEHUB_SCHEDULER_STATE");
    return rc;
}
