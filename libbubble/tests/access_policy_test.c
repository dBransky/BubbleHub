#include "bubblehub/access_policy.h"

#include "test_common.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void set_request(bubblehub_access_request *request, const char *subject, const char *method, const char *path) {
    memset(request, 0, sizeof(*request));
    snprintf(request->kind, sizeof(request->kind), "http");
    snprintf(request->subject, sizeof(request->subject), "%s", subject);
    snprintf(request->method, sizeof(request->method), "%s", method);
    snprintf(request->path, sizeof(request->path), "%s", path);
}

static int test_manifest_path(void) {
    char manifest_path[1024];
    TEST_CHECK(bubblehub_access_manifest_path("agt-policytest", manifest_path, sizeof(manifest_path)) == 0);
    TEST_CHECK(strstr(manifest_path, "access-manifest.json") != NULL);
    return 0;
}

static int test_deny_by_default(void) {
    bubblehub_access_request request;
    set_request(&request, "Example.COM", "GET", "/mail/read");
    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
    TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &request, 0, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_DENY);

    char *pending = bubblehub_access_pending_json();
    TEST_CHECK_CONTAINS(pending, "\"agent_id\":\"agt-policytest\"");
    TEST_CHECK_CONTAINS(pending, "\"subject\":\"example.com\"");
    bubblehub_access_free_string(pending);
    return 0;
}

static int test_always_policy(void) {
    bubblehub_access_request request;
    set_request(&request, "Example.COM", "GET", "/mail/read");
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &request, "always") == 0);
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &request, "approve") != 0);
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &request, "deny") != 0);

    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_DENY;
    TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &request, 0, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_APPROVE);
    for (int i = 0; i < 3; i++) {
        decision = BUBBLEHUB_ACCESS_DECISION_DENY;
        TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &request, 0, &decision) == 0);
        TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_APPROVE);
    }

    char *pending = bubblehub_access_pending_json();
    TEST_CHECK(pending == NULL || strstr(pending, "\"agent_id\":\"agt-policytest\"") == NULL);
    bubblehub_access_free_string(pending);
    return 0;
}

static int test_wildcard_never_and_exact_override(void) {
    bubblehub_access_request wildcard;
    set_request(&wildcard, "*.example.org", "*", "*");
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &wildcard, "never") == 0);

    bubblehub_access_request subdomain;
    set_request(&subdomain, "api.example.org", "POST", "/rpc");
    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
    TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &subdomain, 0, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_DENY);
    for (int i = 0; i < 3; i++) {
        decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
        TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &subdomain, 0, &decision) == 0);
        TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_DENY);
    }

    bubblehub_access_request exact;
    set_request(&exact, "api.example.org", "POST", "/rpc");
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &exact, "always") == 0);
    decision = BUBBLEHUB_ACCESS_DECISION_DENY;
    TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &subdomain, 0, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_APPROVE);
    return 0;
}

static int test_redirect_host_policy(void) {
    bubblehub_access_request redirect_get;
    set_request(&redirect_get, "redirect.example.net", "GET", "/search?q=bubblehub");
    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
    TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &redirect_get, 0, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_DENY);

    bubblehub_access_request redirect_host;
    set_request(&redirect_host, "redirect.example.net", "*", "*");
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &redirect_host, "always") == 0);

    char *pending = bubblehub_access_pending_json();
    TEST_CHECK(pending == NULL || strstr(pending, "\"subject\":\"redirect.example.net\"") == NULL);
    bubblehub_access_free_string(pending);

    bubblehub_access_request redirect_connect;
    set_request(&redirect_connect, "redirect.example.net", "CONNECT", "");
    decision = BUBBLEHUB_ACCESS_DECISION_DENY;
    TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &redirect_connect, 0, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_APPROVE);
    return 0;
}

static int test_ask_policy(void) {
    bubblehub_access_request ask_request;
    set_request(&ask_request, "ask.example.net", "GET", "/again");
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &ask_request, "ask") == 0);

    int needs_prompt = 0;
    TEST_CHECK(bubblehub_access_needs_prompt("agt-policytest", &ask_request, &needs_prompt) == 0);
    TEST_CHECK(needs_prompt);

    bubblehub_access_decision decision;
    for (int i = 0; i < 3; i++) {
        decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
        TEST_CHECK(bubblehub_access_evaluate("agt-policytest", &ask_request, 0, &decision) == 0);
        TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_DENY);
    }
    return 0;
}

static int test_manifest_json(void) {
    bubblehub_access_request request;
    set_request(&request, "manifest.example.com", "GET", "/data");
    TEST_CHECK(bubblehub_access_apply_policy("agt-policytest", &request, "always") == 0);

    char *manifest = bubblehub_access_manifest_json("agt-policytest");
    TEST_CHECK(manifest != NULL);
    TEST_CHECK_CONTAINS(manifest, "\"subject\":\"manifest.example.com\"");
    TEST_CHECK_CONTAINS(manifest, "\"policy\":\"always\"");
    bubblehub_access_free_string(manifest);
    return 0;
}

int main(void) {
    char *state_dir = test_mkdtemp_copy("bubblehub-access-policy-test");
    if (state_dir == NULL) {
        fprintf(stderr, "failed to create temp state dir\n");
        return 1;
    }
    setenv("BUBBLEHUB_STATE_DIR", state_dir, 1);

    int rc = 0;
    rc |= test_manifest_path();
    rc |= test_deny_by_default();
    rc |= test_always_policy();
    rc |= test_wildcard_never_and_exact_override();
    rc |= test_redirect_host_policy();
    rc |= test_ask_policy();
    rc |= test_manifest_json();

    unsetenv("BUBBLEHUB_STATE_DIR");
    free(state_dir);
    return rc;
}
