#define _GNU_SOURCE

#include "bubblehub/hw.h"
#include "bubblehub/log.h"
#include "bubblehub/scheduler.h"

#include "test_common.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void bubblehub_log_init(void) {}
void bubblehub_log_set_level(const char *level) {
    (void)level;
}
void bubblehub_log_set_file(const char *path) {
    (void)path;
}
void bubblehub_log_write(int level, const char *file, int line, const char *text, const char *fmt, ...) {
    (void)level;
    (void)file;
    (void)line;
    (void)text;
    (void)fmt;
}

uint64_t bubblehub_hw_total_ram_bytes(void) {
    return 16ULL * 1024ULL * 1024ULL * 1024ULL;
}

uint64_t bubblehub_hw_vram_bytes(void) {
    return 8ULL * 1024ULL * 1024ULL * 1024ULL;
}

uint64_t bubblehub_hw_free_vram_bytes(void) {
    return 6ULL * 1024ULL * 1024ULL * 1024ULL;
}

#include "../scheduler.c"

static int setup_state_file(void) {
    char *dir = test_mkdtemp_copy("bubblehub-scheduler-internal");
    TEST_CHECK(dir != NULL);
    char path[512];
    snprintf(path, sizeof(path), "%s/scheduler.state", dir);
    setenv("BUBBLEHUB_SCHEDULER_STATE", path, 1);
    free(dir);
    return 0;
}

static int test_state_helpers_and_capacity(void) {
    bubblehub_scheduler_state state;
    init_state(&state);
    TEST_CHECK_EQ(find_agent(&state, "agt-missing"), -1);
    TEST_CHECK_EQ(find_model(&state, "missing"), -1);
    TEST_CHECK_EQ(find_free_agent(&state), 0);
    TEST_CHECK_EQ(find_free_model(&state), 0);
    TEST_CHECK_EQ(find_free_queue(&state), 0);
    TEST_CHECK(strcmp(ram_state(&state, 1.0), "available") == 0);

    state.ram_limit_gb = 4.0;
    state.vram_limit_gb = 2.0;
    state.models[0].active = 1;
    copy_field(state.models[0].name, sizeof(state.models[0].name), "idle-old");
    state.models[0].ram_gb = 3.0;
    state.models[0].vram_gb = 1.5;
    state.models[0].refcount = 0;
    state.models[0].last_used = 1.0;
    state.models[1].active = 1;
    copy_field(state.models[1].name, sizeof(state.models[1].name), "active");
    state.models[1].ram_gb = 0.5;
    state.models[1].vram_gb = 0.1;
    state.models[1].refcount = 1;
    state.models[1].last_used = 2.0;

    TEST_CHECK_EQ(find_lru_idle_model(&state, NULL), 0);
    TEST_CHECK(!has_capacity_for(&state, 1.0, 1.0));
    TEST_CHECK(evict_idle_until_fits(&state, "new", 1.0, 0.2));
    TEST_CHECK(!state.models[0].active);
    TEST_CHECK(state.models[1].active);
    TEST_CHECK(strcmp(ram_state(&state, 3.5), "no_ram") == 0);
    return 0;
}

static int test_locked_public_edge_paths(void) {
    TEST_CHECK(setup_state_file() == 0);
    TEST_CHECK(bubblehub_scheduler_configure_limits(2.0, 1.0) == 0);

    for (int i = 0; i < BUBBLEHUB_MAX_AGENTS; i++) {
        char agent_id[64];
        snprintf(agent_id, sizeof(agent_id), "agt-fill-%d", i);
        TEST_CHECK(bubblehub_scheduler_register_agent(agent_id, 1000 + i, "/bin/agent", i, "general") == 0);
    }
    TEST_CHECK(bubblehub_scheduler_register_agent("agt-overflow", 1, "/bin/agent", 0, "general") == -1);

    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        char name[64];
        snprintf(name, sizeof(name), "model-%d", i);
        TEST_CHECK(bubblehub_scheduler_mark_model_loaded(name, "general", "llama", 0.01, 0.0, 0, 8000 + i) == 0);
    }
    TEST_CHECK(bubblehub_scheduler_mark_model_loaded("model-overflow", "general", "llama", 0.01, 0.0, 0, 9000) == -1);

    int allowed = 1;
    char state[64];
    char reason[256];
    TEST_CHECK(bubblehub_scheduler_admit_model_job("general", "too-large", 10, 16.0, 0.0, &allowed, state, sizeof(state), reason, sizeof(reason)) == 0);
    TEST_CHECK(!allowed);
    TEST_CHECK_CONTAINS(reason, "not enough RAM");
    TEST_CHECK(bubblehub_scheduler_admit_model_job("general", "bad", 0, 1.0, 0.0, NULL, state, sizeof(state), reason, sizeof(reason)) == -1);

    TEST_CHECK(bubblehub_scheduler_deregister_agent(NULL) == -1);
    TEST_CHECK(bubblehub_scheduler_mark_model_unloaded(NULL) == -1);
    TEST_CHECK(bubblehub_scheduler_evict_model(NULL) == -1);
    TEST_CHECK(bubblehub_scheduler_add_queue_item(NULL, "kind", "general", "model", 0, "reason") == -1);
    unsetenv("BUBBLEHUB_SCHEDULER_STATE");
    return 0;
}

static int test_json_builder_and_request_parsing(void) {
    char small[8];
    bubblehub_json_builder builder = {
        .data = small,
        .len = 0,
        .cap = sizeof(small),
        .failed = 0,
    };
    json_append(&builder, "123456789");
    TEST_CHECK(builder.failed);

    char escaped[128];
    builder = (bubblehub_json_builder){.data = escaped, .len = 0, .cap = sizeof(escaped), .failed = 0};
    json_string(&builder, "a\"b\\c\n");
    TEST_CHECK_STR(escaped, "\"a\\\"b\\\\c\"");

    char value[64];
    TEST_CHECK(json_get_string_field("{\"name\":\"a\\n\\\"b\"}", "name", value, sizeof(value)) == 0);
    TEST_CHECK_STR(value, "a\n\"b");
    TEST_CHECK(json_get_string_field("{\"name\":12}", "name", value, sizeof(value)) == -1);
    TEST_CHECK_EQ(json_get_int_field("{\"value\":42}", "value", 1), 42);
    TEST_CHECK_EQ((int)json_get_double_field("{\"value\":2.5}", "value", 1.0), 2);

    bubblehub_chat_request request;
    const char *raw =
        "{\"specialty\":\"general\",\"model_name\":\"small\",\"backend\":\"llama\",\"model_path\":\"/tmp/model.gguf\","
        "\"messages_json\":\"[{\\\"role\\\":\\\"user\\\",\\\"content\\\":\\\"hi\\\"}]\",\"ram_gb\":1.5,"
        "\"vram_gb\":0.5,\"niceness\":3,\"max_tokens\":99,\"gpu_layers\":12}";
    TEST_CHECK(parse_chat_request(raw, &request) == 0);
    TEST_CHECK_STR(request.model_name, "small");
    TEST_CHECK_EQ(request.max_tokens, 99);
    TEST_CHECK_EQ(request.gpu_layers, 12);
    TEST_CHECK(parse_chat_request("{}", &request) == -1);

    char *error = json_response_error(NULL);
    TEST_CHECK(error != NULL);
    TEST_CHECK_CONTAINS(error, "native inference failed");
    free(error);
    return 0;
}

static int test_network_and_inference_helpers(void) {
    int port = 0;
    TEST_CHECK(parse_local_http_base("http://127.0.0.1:1234", &port) == 0);
    TEST_CHECK_EQ(port, 1234);
    TEST_CHECK(parse_local_http_base("localhost", &port) == 0);
    TEST_CHECK_EQ(port, 80);
    TEST_CHECK(parse_local_http_base("http://example.com:1234", &port) == -1);

    unsetenv("BUBBLEHUB_NETWORK");
    unsetenv("BUBBLEHUB_API_BASE_URL");
    TEST_CHECK(!native_should_forward_to_sandbox_endpoint());
    setenv("BUBBLEHUB_NETWORK", "inference-only", 1);
    setenv("BUBBLEHUB_API_BASE_URL", "http://127.0.0.1:18000", 1);
    TEST_CHECK(native_should_forward_to_sandbox_endpoint());

    TEST_CHECK(pid_is_running(0) == 0);
    TEST_CHECK(pid_is_running(getpid()) == 1);
    TEST_CHECK(connect_localhost(1) == -1);
    int port_candidate = allocate_local_port();
    TEST_CHECK(port_candidate > 0);

    char response[] = "HTTP/1.1 200 OK\r\n\r\n{\"choices\":[{\"message\":{\"content\":\"hello\"}}]}";
    char content[64];
    TEST_CHECK(extract_chat_content(response, content, sizeof(content)) == 0);
    TEST_CHECK_STR(content, "hello");
    TEST_CHECK(extract_chat_content("no-body", content, sizeof(content)) == -1);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_state_helpers_and_capacity();
    rc |= test_locked_public_edge_paths();
    rc |= test_json_builder_and_request_parsing();
    rc |= test_network_and_inference_helpers();
    return rc;
}
