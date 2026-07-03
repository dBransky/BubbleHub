#define _GNU_SOURCE

#include "bubblehub/access_policy.h"
#include "bubblehub/http_proxy.h"
#include "bubblehub/log.h"

#include "test_common.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int g_needs_prompt = 0;
static int g_policy_rc = 0;
static bubblehub_access_decision g_policy_decision = BUBBLEHUB_ACCESS_DECISION_DENY;

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

int bubblehub_access_evaluate(
    const char *agent_id,
    const bubblehub_access_request *request,
    int allow_prompt,
    bubblehub_access_decision *decision) {
    (void)agent_id;
    (void)request;
    (void)allow_prompt;
    if (decision != NULL) {
        *decision = g_policy_decision;
    }
    return g_policy_rc;
}

int bubblehub_access_needs_prompt(
    const char *agent_id,
    const bubblehub_access_request *request,
    int *needs_prompt) {
    (void)agent_id;
    (void)request;
    if (needs_prompt != NULL) {
        *needs_prompt = g_needs_prompt;
    }
    return 0;
}

#include "../http_proxy.c"

static int test_string_and_url_helpers(void) {
    char buffer[16];
    TEST_CHECK(copy_token(NULL, sizeof(buffer), "abc", 3) == -EINVAL);
    TEST_CHECK(copy_token(buffer, 0, "abc", 3) == -EINVAL);
    TEST_CHECK(copy_token(buffer, sizeof(buffer), "abcdef", 3) == 0);
    TEST_CHECK_STR(buffer, "abc");
    TEST_CHECK(copy_string(buffer, sizeof(buffer), "Mixed") == 0);
    lowercase_ascii(buffer);
    TEST_CHECK_STR(buffer, "mixed");

    const char request[] = "GET /one HTTP/1.1\r\nHost: Example.COM:8080\r\n\r\n";
    const char *end = request + strlen(request);
    const char *line_end = find_line_end(request, end);
    TEST_CHECK(line_end != NULL);
    TEST_CHECK(trim_line_end(request, line_end) == line_end - 1);
    TEST_CHECK(skip_spaces(" \tvalue", " \tvalue" + 7)[0] == 'v');
    TEST_CHECK(find_space("abc def", "abc def" + 7)[0] == ' ');
    TEST_CHECK(starts_with_scheme("https://example", strlen("https://example")));
    TEST_CHECK(default_port_for_scheme("https", 5) == 443U);
    TEST_CHECK(default_port_for_scheme("http", 4) == 80U);
    return 0;
}

static int test_parse_variants_and_failures(void) {
    bubblehub_http_proxy_request parsed;
    TEST_CHECK(bubblehub_http_proxy_parse_request(NULL, 0, &parsed) == -EINVAL);
    TEST_CHECK(bubblehub_http_proxy_parse_request("GET / HTTP/1.1", strlen("GET / HTTP/1.1"), &parsed) == -EINVAL);
    TEST_CHECK(bubblehub_http_proxy_parse_request("GET / HTTP/1.1\r\n\r\n", strlen("GET / HTTP/1.1\r\n\r\n"), &parsed) == -EINVAL);
    TEST_CHECK(bubblehub_http_proxy_parse_request("GET http://[::1]:8080/path HTTP/1.1\r\n\r\n", strlen("GET http://[::1]:8080/path HTTP/1.1\r\n\r\n"), &parsed) == 0);
    TEST_CHECK_STR(parsed.host, "::1");
    TEST_CHECK_EQ(parsed.port, 8080U);
    TEST_CHECK(bubblehub_http_proxy_parse_request("CONNECT [::1]:443 HTTP/1.1\r\n\r\n", strlen("CONNECT [::1]:443 HTTP/1.1\r\n\r\n"), &parsed) == 0);
    TEST_CHECK(parsed.is_connect);
    TEST_CHECK(split_host_port("example.com:bad", strlen("example.com:bad"), 80, parsed.host, sizeof(parsed.host), &parsed.port) == -EINVAL);
    TEST_CHECK(split_host_port("a:b:c", strlen("a:b:c"), 80, parsed.host, sizeof(parsed.host), &parsed.port) == 0);
    TEST_CHECK_STR(parsed.host, "a:b:c");
    return 0;
}

static int test_json_and_policy_helpers(void) {
    char buffer[128] = "";
    size_t offset = 0;
    TEST_CHECK(append_text(buffer, sizeof(buffer), &offset, "{") == 0);
    TEST_CHECK(append_json_field(buffer, sizeof(buffer), &offset, "quote", "a\"b\\c\n", 1) == 0);
    TEST_CHECK(append_text(buffer, sizeof(buffer), &offset, "}") == 0);
    TEST_CHECK_CONTAINS(buffer, "\\\"b\\\\c\\n");
    TEST_CHECK(append_text(buffer, 4, &offset, "too-long") == -ENOSPC);

    bubblehub_http_proxy_request parsed;
    memset(&parsed, 0, sizeof(parsed));
    snprintf(parsed.method, sizeof(parsed.method), "GET");
    snprintf(parsed.host, sizeof(parsed.host), "example.com");
    snprintf(parsed.path, sizeof(parsed.path), "/path");
    bubblehub_access_request request;
    TEST_CHECK(policy_request_from_http(&parsed, &request) == 0);
    TEST_CHECK_STR(request.kind, "http");
    TEST_CHECK_STR(request.subject, "example.com");

    parsed.host[0] = '\0';
    TEST_CHECK(policy_request_from_http(&parsed, &request) == -EINVAL);
    return 0;
}

static int test_broker_response_and_decision(void) {
    char response[16];
    TEST_CHECK(read_broker_response(-1, NULL, sizeof(response)) == -EINVAL);

    int fds[2];
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);
    TEST_CHECK(write(fds[0], "approve\r\n", strlen("approve\r\n")) > 0);
    TEST_CHECK(read_broker_response(fds[1], response, sizeof(response)) == 0);
    TEST_CHECK_STR(response, "approve");
    close(fds[0]);
    close(fds[1]);

    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);
    bubblehub_access_request request;
    memset(&request, 0, sizeof(request));
    snprintf(request.kind, sizeof(request.kind), "http");
    snprintf(request.subject, sizeof(request.subject), "example.com");
    snprintf(request.method, sizeof(request.method), "GET");
    snprintf(request.path, sizeof(request.path), "/");
    TEST_CHECK(write(fds[1], "deny\n", strlen("deny\n")) > 0);
    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
    TEST_CHECK(request_broker_decision(fds[0], "agt-test", &request, &decision) == 0);
    TEST_CHECK(decision == BUBBLEHUB_ACCESS_DECISION_DENY);
    close(fds[0]);
    close(fds[1]);

    TEST_CHECK(request_broker_decision(-1, "agt-test", &request, &decision) == -EINVAL);
    return 0;
}

static int test_client_denial_paths(void) {
    int fds[2];
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);
    TEST_CHECK(write(fds[0], "not-http\r\n", strlen("not-http\r\n")) > 0);
    TEST_CHECK(bubblehub_http_proxy_handle_client_for_agent_broker(fds[1], "agt-test", -1) == 0);
    char denied[512];
    ssize_t got = read(fds[0], denied, sizeof(denied) - 1);
    TEST_CHECK(got > 0);
    denied[got] = '\0';
    TEST_CHECK_CONTAINS(denied, "403 Forbidden");
    close(fds[0]);
    close(fds[1]);

    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);
    TEST_CHECK(write(fds[0], "GET http://127.0.0.1:1/ HTTP/1.1\r\n\r\n", strlen("GET http://127.0.0.1:1/ HTTP/1.1\r\n\r\n")) > 0);
    g_policy_decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
    TEST_CHECK(bubblehub_http_proxy_handle_client_for_agent_broker(fds[1], "agt-test", -1) == 0);
    got = read(fds[0], denied, sizeof(denied) - 1);
    TEST_CHECK(got > 0);
    denied[got] = '\0';
    TEST_CHECK_CONTAINS(denied, "403 Forbidden");
    close(fds[0]);
    close(fds[1]);
    g_policy_decision = BUBBLEHUB_ACCESS_DECISION_DENY;
    return 0;
}

static int test_listener_and_ready_helpers(void) {
    TEST_CHECK(connect_to_upstream(NULL, 80) == -EINVAL);
    TEST_CHECK(connect_to_upstream("localhost", 0) == -EINVAL);
    TEST_CHECK(create_loopback_listener(0) == -EINVAL);
    int listener = create_loopback_listener(1);
    if (listener >= 0) {
        close(listener);
    }

    int fds[2];
    TEST_CHECK(pipe(fds) == 0);
    TEST_CHECK(signal_proxy_ready(fds[1]) == 0);
    char byte = 0;
    TEST_CHECK(read(fds[0], &byte, 1) == 1);
    close(fds[0]);
    close(fds[1]);
    TEST_CHECK(signal_proxy_ready(-1) == 0);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_string_and_url_helpers();
    rc |= test_parse_variants_and_failures();
    rc |= test_json_and_policy_helpers();
    rc |= test_broker_response_and_decision();
    rc |= test_client_denial_paths();
    rc |= test_listener_and_ready_helpers();
    return rc;
}
