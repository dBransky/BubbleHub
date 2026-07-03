#define _GNU_SOURCE

#include "bubblehub/access_policy.h"
#include "bubblehub/log.h"

#include "test_common.h"

#include <errno.h>
#include <stdarg.h>
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

#include "../access_policy.c"

static void fill_request(bubblehub_access_request *request, const char *kind, const char *subject, const char *method, const char *path) {
    memset(request, 0, sizeof(*request));
    snprintf(request->kind, sizeof(request->kind), "%s", kind);
    snprintf(request->subject, sizeof(request->subject), "%s", subject);
    snprintf(request->method, sizeof(request->method), "%s", method);
    snprintf(request->path, sizeof(request->path), "%s", path);
}

static int test_normalization_ids_and_paths(void) {
    char field[8];
    copy_field(field, sizeof(field), NULL);
    TEST_CHECK_STR(field, "");
    copy_field(field, sizeof(field), "abcdefghijk");
    TEST_CHECK_STR(field, "abcdefg");

    normalize_token(field, sizeof(field), "  MiXeD  ", 1);
    TEST_CHECK_STR(field, "mixed");
    normalize_token(field, sizeof(field), NULL, 1);
    TEST_CHECK_STR(field, "");

    TEST_CHECK(is_valid_agent_id("agt-good_1"));
    TEST_CHECK(!is_valid_agent_id(NULL));
    TEST_CHECK(!is_valid_agent_id("bad"));
    TEST_CHECK(!is_valid_agent_id("agt-"));
    TEST_CHECK(!is_valid_agent_id("agt-bad.dot"));

    unsigned long parsed = 0;
    TEST_CHECK(parse_owner_id("42", &parsed));
    TEST_CHECK_EQ(parsed, 42UL);
    TEST_CHECK(!parse_owner_id("", &parsed));
    TEST_CHECK(!parse_owner_id("bad", &parsed));
    TEST_CHECK(!parse_owner_id("42x", &parsed));

    char path[1024];
    TEST_CHECK(bubblehub_access_manifest_path("bad", path, sizeof(path)) == -EINVAL);
    TEST_CHECK(bubblehub_access_manifest_path("agt-good", NULL, sizeof(path)) == -EINVAL);
    return 0;
}

static int test_json_parsing_and_manifest_roundtrip(void) {
    const char *json =
        "{\"agent_id\":\"agt-json\",\"policies\":["
        "{\"kind\":\"HTTP\",\"subject\":\"Example.COM\",\"method\":\"GET\",\"path\":\"/one\",\"policy\":\"ALWAYS\"},"
        "{\"kind\":\"\",\"subject\":\"bad\",\"policy\":\"always\"}],"
        "\"pending\":[{\"id\":\"req-1\",\"kind\":\"http\",\"subject\":\"pending.example\",\"method\":\"POST\",\"path\":\"/two\",\"created_at\":123}]}";
    bubblehub_access_manifest manifest;
    manifest_init(&manifest, "agt-json");
    char value[64];
    TEST_CHECK(json_get_string(json, "agent_id", value, sizeof(value)));
    TEST_CHECK_STR(value, "agt-json");
    TEST_CHECK_EQ((int)json_get_number(json, "created_at"), 123);
    parse_policies(json, &manifest);
    parse_pending(json, &manifest);
    TEST_CHECK_EQ(manifest.policy_count, (size_t)1);
    TEST_CHECK_EQ(manifest.pending_count, (size_t)1);
    TEST_CHECK_STR(manifest.policies[0].request.subject, "example.com");
    TEST_CHECK_STR(manifest.policies[0].policy, "always");

    char *serialized = manifest_to_json(&manifest);
    TEST_CHECK(serialized != NULL);
    TEST_CHECK_CONTAINS(serialized, "\"agent_id\": \"agt-json\"");
    TEST_CHECK_CONTAINS(serialized, "\"pending\"");
    free(serialized);

    const char *array_end = NULL;
    TEST_CHECK(find_array("{\"items\":[{\"x\":\"[not-end]\"}]}", "items", &array_end) != NULL);
    TEST_CHECK(find_array("{}", "missing", &array_end) == NULL);
    TEST_CHECK(next_object("  []", "  []" + 4, &array_end) == NULL);
    return 0;
}

static int test_matching_and_pending_helpers(void) {
    bubblehub_access_manifest manifest;
    manifest_init(&manifest, "agt-match");
    bubblehub_access_request raw_request;
    bubblehub_access_request request;
    fill_request(&raw_request, "http", "api.example.org", "POST", "/rpc");
    normalize_request(&request, &raw_request);

    fill_request(&manifest.policies[0].request, "http", "*.example.org", "*", "*");
    snprintf(manifest.policies[0].policy, sizeof(manifest.policies[0].policy), "never");
    fill_request(&manifest.policies[1].request, "http", "api.example.org", "POST", "/rpc");
    snprintf(manifest.policies[1].policy, sizeof(manifest.policies[1].policy), "always");
    manifest.policy_count = 2;

    TEST_CHECK(subject_matches("*.example.org", "api.example.org"));
    TEST_CHECK(!subject_matches("*.example.org", "example.org"));
    TEST_CHECK(method_matches("*", "GET"));
    TEST_CHECK(method_matches("", "GET"));
    TEST_CHECK(path_matches("*", "/rpc"));
    TEST_CHECK(request_matches_rule(&manifest.policies[0].request, &request));
    TEST_CHECK(find_policy(&manifest, &request) == &manifest.policies[1]);

    TEST_CHECK(add_pending(&manifest, &request) == 0);
    TEST_CHECK(add_pending(&manifest, &request) == 0);
    TEST_CHECK_EQ(manifest.pending_count, (size_t)1);
    TEST_CHECK(remove_pending(&manifest, &request));
    TEST_CHECK_EQ(manifest.pending_count, (size_t)0);

    memset(manifest.pending, 0, sizeof(manifest.pending));
    manifest.pending_count = BUBBLEHUB_ACCESS_MAX_PENDING;
    TEST_CHECK(add_pending(&manifest, &request) == -ENOSPC);
    TEST_CHECK(is_policy_value("ask"));
    TEST_CHECK(!is_policy_value("sometimes"));
    return 0;
}

static int test_builder_and_public_invalid_paths(void) {
    char tiny[4];
    bubblehub_json_builder builder;
    json_builder_init(&builder, tiny, sizeof(tiny));
    json_append(&builder, "too long");
    TEST_CHECK(builder.failed);

    char escaped[128];
    json_builder_init(&builder, escaped, sizeof(escaped));
    json_append_escaped(&builder, "a\"b\\c\n\t");
    TEST_CHECK_CONTAINS(escaped, "\\\"b\\\\c\\n\\t");

    bubblehub_access_request request;
    fill_request(&request, "", "example.com", "GET", "/");
    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
    TEST_CHECK(bubblehub_access_apply_policy("bad", &request, "always") == -EINVAL);
    TEST_CHECK(bubblehub_access_apply_policy("agt-test", NULL, "always") == -EINVAL);
    TEST_CHECK(bubblehub_access_apply_policy("agt-test", &request, "always") == -EINVAL);
    TEST_CHECK(bubblehub_access_evaluate("bad", &request, 0, &decision) == -EINVAL);
    TEST_CHECK(bubblehub_access_needs_prompt("bad", &request, NULL) == -EINVAL);
    TEST_CHECK(bubblehub_access_manifest_json("bad") == NULL);
    return 0;
}

int main(void) {
    char *state_dir = test_mkdtemp_copy("bubblehub-access-policy-internal");
    TEST_CHECK(state_dir != NULL);
    setenv("BUBBLEHUB_STATE_DIR", state_dir, 1);

    int rc = 0;
    rc |= test_normalization_ids_and_paths();
    rc |= test_json_parsing_and_manifest_roundtrip();
    rc |= test_matching_and_pending_helpers();
    rc |= test_builder_and_public_invalid_paths();

    unsetenv("BUBBLEHUB_STATE_DIR");
    free(state_dir);
    return rc;
}
