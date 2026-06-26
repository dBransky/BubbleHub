#include "ageos/access_policy.h"
#include "ageos/http_proxy.h"

#include "test_common.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __linux__
#include <errno.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

static int expect_request(const char *raw, const char *method, const char *url) {
    ageos_http_proxy_request parsed;
    int rc = ageos_http_proxy_parse_request(raw, strlen(raw), &parsed);
    TEST_CHECK(rc == 0);
    TEST_CHECK_STR(parsed.method, method);
    TEST_CHECK_STR(parsed.url, url);
    return 0;
}

static int expect_request_target(
    const char *raw,
    const char *method,
    const char *url,
    const char *host,
    const char *path,
    unsigned int port,
    int is_connect) {
    ageos_http_proxy_request parsed;
    int rc = ageos_http_proxy_parse_request(raw, strlen(raw), &parsed);
    TEST_CHECK(rc == 0);
    TEST_CHECK_STR(parsed.method, method);
    TEST_CHECK_STR(parsed.url, url);
    TEST_CHECK_STR(parsed.host, host);
    TEST_CHECK_STR(parsed.path, path);
    TEST_CHECK_EQ(parsed.port, port);
    TEST_CHECK_EQ(parsed.is_connect, is_connect);
    return 0;
}

static int test_tcp_port_is_valid(void) {
    TEST_CHECK(!ageos_tcp_port_is_valid(0U));
    TEST_CHECK(ageos_tcp_port_is_valid(1U));
    TEST_CHECK(ageos_tcp_port_is_valid(AGEOS_HTTP_PROXY_DEFAULT_PORT));
    TEST_CHECK(ageos_tcp_port_is_valid(AGEOS_TCP_PORT_MAX));
    TEST_CHECK(!ageos_tcp_port_is_valid(AGEOS_TCP_PORT_MAX + 1U));
    return 0;
}

static int test_parse_request_variants(void) {
    int rc = 0;
    rc |= expect_request(
        "GET http://example.com/path?q=1 HTTP/1.1\r\nHost: ignored.example\r\n\r\n",
        "GET",
        "http://example.com/path?q=1");
    rc |= expect_request_target(
        "GET http://Example.com:8080/path?q=1 HTTP/1.1\r\nHost: ignored.example\r\n\r\n",
        "GET",
        "http://Example.com:8080/path?q=1",
        "example.com",
        "/path?q=1",
        8080U,
        0);
    rc |= expect_request(
        "GET https://example.com/secure HTTP/1.1\r\nHost: example.com\r\n\r\n",
        "GET",
        "https://example.com/secure");
    rc |= expect_request(
        "CONNECT mcp.example.test:443 HTTP/1.1\r\nHost: mcp.example.test:443\r\n\r\n",
        "CONNECT",
        "mcp.example.test:443");
    rc |= expect_request_target(
        "CONNECT mcp.example.test:443 HTTP/1.1\r\nHost: mcp.example.test:443\r\n\r\n",
        "CONNECT",
        "mcp.example.test:443",
        "mcp.example.test",
        "",
        443U,
        1);
    rc |= expect_request(
        "POST /rpc HTTP/1.1\r\nHost: 127.0.0.1:9000\r\nContent-Length: 2\r\n\r\n{}",
        "POST",
        "http://127.0.0.1:9000/rpc");

    ageos_http_proxy_request parsed;
    TEST_CHECK(ageos_http_proxy_parse_request("not-http\r\n", strlen("not-http\r\n"), &parsed) != 0);
    return rc;
}

#ifdef __linux__
static int expect_denied_response(int client_fd) {
    char buffer[512];
    ssize_t received = read(client_fd, buffer, sizeof(buffer) - 1);
    TEST_CHECK(received > 0);
    buffer[received] = '\0';
    TEST_CHECK(strstr(buffer, "403 Forbidden") != NULL);
    TEST_CHECK(strstr(buffer, "AgeOS proxy denied the request") != NULL);
    return 0;
}

static int test_handle_client_denies_get(void) {
    int fds[2];
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);

    const char *request = "GET http://example.com/proxy-test HTTP/1.1\r\n\r\n";
    TEST_CHECK(write(fds[0], request, strlen(request)) >= 0);

    int rc = ageos_http_proxy_handle_client(fds[1]);
    close(fds[1]);
    TEST_CHECK(rc == 0);
    rc = expect_denied_response(fds[0]);
    close(fds[0]);
    return rc;
}

static int test_handle_client_uses_trusted_agent_id_not_env(void) {
    char *state_dir = test_mkdtemp_copy("ageos-http-proxy-policy-test");
    TEST_CHECK(state_dir != NULL);
    setenv("AGEOS_STATE_DIR", state_dir, 1);

    ageos_access_request always;
    memset(&always, 0, sizeof(always));
    snprintf(always.kind, sizeof(always.kind), "http");
    snprintf(always.subject, sizeof(always.subject), "example.com");
    snprintf(always.method, sizeof(always.method), "GET");
    snprintf(always.path, sizeof(always.path), "/manifest-impersonation");
    TEST_CHECK(ageos_access_apply_policy("agt-victim-manifest", &always, "always") == 0);
    setenv("AGEOS_AGENT_ID", "agt-victim-manifest", 1);

    int fds[2];
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == 0);
    const char *request = "GET http://example.com/manifest-impersonation HTTP/1.1\r\n\r\n";
    TEST_CHECK(write(fds[0], request, strlen(request)) >= 0);

    int rc = ageos_http_proxy_handle_client_for_agent(fds[1], "agt-real-manifest");
    close(fds[1]);
    TEST_CHECK(rc == 0);
    rc = expect_denied_response(fds[0]);
    close(fds[0]);
    unsetenv("AGEOS_AGENT_ID");
    unsetenv("AGEOS_STATE_DIR");
    free(state_dir);
    return rc;
}

static int test_broker_prompts_when_pending_write_would_fail(void) {
    char *state_dir = test_mkdtemp_copy("ageos-http-proxy-broker-test");
    TEST_CHECK(state_dir != NULL);
    setenv("AGEOS_STATE_DIR", state_dir, 1);

    char manifest_path[1024];
    TEST_CHECK(ageos_access_manifest_path("agt-brokerwrite", manifest_path, sizeof(manifest_path)) == 0);
    TEST_CHECK(mkdir(manifest_path, 0700) == 0);

    int client_fds[2];
    int broker_fds[2];
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, client_fds) == 0);
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, broker_fds) == 0);

    const char *request = "GET http://google.com/ HTTP/1.1\r\nHost: google.com\r\n\r\n";
    TEST_CHECK(write(client_fds[0], request, strlen(request)) >= 0);

    pid_t pid = fork();
    TEST_CHECK(pid >= 0);
    if (pid == 0) {
        close(client_fds[0]);
        close(broker_fds[0]);
        int child_rc = ageos_http_proxy_handle_client_for_agent_broker(client_fds[1], "agt-brokerwrite", broker_fds[1]);
        close(client_fds[1]);
        close(broker_fds[1]);
        _exit(child_rc == 0 ? 0 : 2);
    }
    close(client_fds[1]);
    close(broker_fds[1]);

    fd_set reads;
    FD_ZERO(&reads);
    FD_SET(broker_fds[0], &reads);
    struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
    int ready = select(broker_fds[0] + 1, &reads, NULL, NULL, &timeout);
    TEST_CHECK(ready > 0);

    char broker_request[512];
    ssize_t received = read(broker_fds[0], broker_request, sizeof(broker_request) - 1);
    TEST_CHECK(received > 0);
    broker_request[received] = '\0';
    TEST_CHECK(strstr(broker_request, "\"subject\":\"google.com\"") != NULL);
    TEST_CHECK(write(broker_fds[0], "deny\n", strlen("deny\n")) >= 0);

    int rc = expect_denied_response(client_fds[0]);
    close(client_fds[0]);
    close(broker_fds[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    unsetenv("AGEOS_STATE_DIR");
    free(state_dir);
    TEST_CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    return rc;
}

static int test_serve_rejects_invalid_listen_port(void) {
    TEST_CHECK_EQ(ageos_http_proxy_serve(0U, -1), -EINVAL);
    TEST_CHECK_EQ(ageos_http_proxy_serve(65536U, -1), -EINVAL);
    TEST_CHECK_EQ(ageos_http_proxy_serve(70000U, -1), -EINVAL);
    return 0;
}
#endif

int main(void) {
    int rc = 0;
    rc |= test_tcp_port_is_valid();
    rc |= test_parse_request_variants();
#ifdef __linux__
    rc |= test_handle_client_denies_get();
    rc |= test_handle_client_uses_trusted_agent_id_not_env();
    rc |= test_broker_prompts_when_pending_write_would_fail();
    rc |= test_serve_rejects_invalid_listen_port();
#endif
    return rc;
}
