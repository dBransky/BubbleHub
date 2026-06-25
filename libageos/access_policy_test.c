#include "ageos/access_policy.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void set_request(ageos_access_request *request, const char *subject, const char *method, const char *path) {
    memset(request, 0, sizeof(*request));
    snprintf(request->kind, sizeof(request->kind), "http");
    snprintf(request->subject, sizeof(request->subject), "%s", subject);
    snprintf(request->method, sizeof(request->method), "%s", method);
    snprintf(request->path, sizeof(request->path), "%s", path);
}

static int contains(const char *haystack, const char *needle) {
    return haystack != NULL && strstr(haystack, needle) != NULL;
}

int main(void) {
    char temp_template[] = "/tmp/ageos-access-policy-test-XXXXXX";
    char *state_dir = mkdtemp(temp_template);
    if (state_dir == NULL) {
        fprintf(stderr, "failed to create temp state dir\n");
        return 1;
    }
    setenv("AGEOS_STATE_DIR", state_dir, 1);

    char manifest_path[1024];
    if (ageos_access_manifest_path("agt-policytest", manifest_path, sizeof(manifest_path)) != 0) {
        fprintf(stderr, "failed to resolve manifest path\n");
        return 1;
    }
    if (strstr(manifest_path, "access-manifest.json") == NULL) {
        fprintf(stderr, "unexpected manifest path: %s\n", manifest_path);
        return 1;
    }

    ageos_access_request request;
    set_request(&request, "Example.COM", "GET", "/mail/read");
    ageos_access_decision decision = AGEOS_ACCESS_DECISION_APPROVE;
    if (ageos_access_evaluate("agt-policytest", &request, 0, &decision) != 0) {
        fprintf(stderr, "evaluate missing policy failed\n");
        return 1;
    }
    if (decision != AGEOS_ACCESS_DECISION_DENY) {
        fprintf(stderr, "missing policy should deny\n");
        return 1;
    }
    char *pending = ageos_access_pending_json();
    if (!contains(pending, "\"agent_id\":\"agt-policytest\"") || !contains(pending, "\"subject\":\"example.com\"")) {
        fprintf(stderr, "pending JSON missing request: %s\n", pending != NULL ? pending : "<null>");
        ageos_access_free_string(pending);
        return 1;
    }
    ageos_access_free_string(pending);

    if (ageos_access_apply_policy("agt-policytest", &request, "always") != 0) {
        fprintf(stderr, "failed to apply always policy\n");
        return 1;
    }
    if (ageos_access_apply_policy("agt-policytest", &request, "approve") == 0 ||
        ageos_access_apply_policy("agt-policytest", &request, "deny") == 0) {
        fprintf(stderr, "legacy approve/deny policies should be rejected\n");
        return 1;
    }
    decision = AGEOS_ACCESS_DECISION_DENY;
    if (ageos_access_evaluate("agt-policytest", &request, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_APPROVE) {
        fprintf(stderr, "always policy did not approve\n");
        return 1;
    }
    for (int i = 0; i < 3; i++) {
        decision = AGEOS_ACCESS_DECISION_DENY;
        if (ageos_access_evaluate("agt-policytest", &request, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_APPROVE) {
            fprintf(stderr, "always policy failed repeated evaluation %d\n", i);
            return 1;
        }
    }
    pending = ageos_access_pending_json();
    if (contains(pending, "\"agent_id\":\"agt-policytest\"")) {
        fprintf(stderr, "pending request was not cleared: %s\n", pending);
        ageos_access_free_string(pending);
        return 1;
    }
    ageos_access_free_string(pending);

    ageos_access_request wildcard;
    set_request(&wildcard, "*.example.org", "*", "*");
    if (ageos_access_apply_policy("agt-policytest", &wildcard, "never") != 0) {
        fprintf(stderr, "failed to apply wildcard never\n");
        return 1;
    }
    ageos_access_request subdomain;
    set_request(&subdomain, "api.example.org", "POST", "/rpc");
    decision = AGEOS_ACCESS_DECISION_APPROVE;
    if (ageos_access_evaluate("agt-policytest", &subdomain, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_DENY) {
        fprintf(stderr, "wildcard never did not match subdomain\n");
        return 1;
    }
    for (int i = 0; i < 3; i++) {
        decision = AGEOS_ACCESS_DECISION_APPROVE;
        if (ageos_access_evaluate("agt-policytest", &subdomain, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_DENY) {
            fprintf(stderr, "never policy failed repeated evaluation %d\n", i);
            return 1;
        }
    }

    ageos_access_request exact;
    set_request(&exact, "api.example.org", "POST", "/rpc");
    if (ageos_access_apply_policy("agt-policytest", &exact, "always") != 0) {
        fprintf(stderr, "failed to apply exact always\n");
        return 1;
    }
    decision = AGEOS_ACCESS_DECISION_DENY;
    if (ageos_access_evaluate("agt-policytest", &subdomain, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_APPROVE) {
        fprintf(stderr, "exact always did not override wildcard never\n");
        return 1;
    }

    ageos_access_request redirect_get;
    set_request(&redirect_get, "redirect.example.net", "GET", "/search?q=ageos");
    decision = AGEOS_ACCESS_DECISION_APPROVE;
    if (ageos_access_evaluate("agt-policytest", &redirect_get, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_DENY) {
        fprintf(stderr, "redirect GET should become pending\n");
        return 1;
    }
    ageos_access_request redirect_host;
    set_request(&redirect_host, "redirect.example.net", "*", "*");
    if (ageos_access_apply_policy("agt-policytest", &redirect_host, "always") != 0) {
        fprintf(stderr, "failed to apply redirect host wildcard policy\n");
        return 1;
    }
    pending = ageos_access_pending_json();
    if (contains(pending, "\"subject\":\"redirect.example.net\"")) {
        fprintf(stderr, "host wildcard policy did not clear covered pending request: %s\n", pending);
        ageos_access_free_string(pending);
        return 1;
    }
    ageos_access_free_string(pending);
    ageos_access_request redirect_connect;
    set_request(&redirect_connect, "redirect.example.net", "CONNECT", "");
    decision = AGEOS_ACCESS_DECISION_DENY;
    if (ageos_access_evaluate("agt-policytest", &redirect_connect, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_APPROVE) {
        fprintf(stderr, "host wildcard policy did not approve CONNECT redirect\n");
        return 1;
    }

    ageos_access_request ask_request;
    set_request(&ask_request, "ask.example.net", "GET", "/again");
    if (ageos_access_apply_policy("agt-policytest", &ask_request, "ask") != 0) {
        fprintf(stderr, "failed to apply ask policy\n");
        return 1;
    }
    int needs_prompt = 0;
    if (ageos_access_needs_prompt("agt-policytest", &ask_request, &needs_prompt) != 0 || !needs_prompt) {
        fprintf(stderr, "ask policy should require prompt\n");
        return 1;
    }
    for (int i = 0; i < 3; i++) {
        decision = AGEOS_ACCESS_DECISION_APPROVE;
        if (ageos_access_evaluate("agt-policytest", &ask_request, 0, &decision) != 0 || decision != AGEOS_ACCESS_DECISION_DENY) {
            fprintf(stderr, "ask policy without prompt failed closed on repeated evaluation %d\n", i);
            return 1;
        }
    }

    unsetenv("AGEOS_STATE_DIR");
    return 0;
}
