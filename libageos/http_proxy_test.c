#include "ageos/access_policy.h"
#include "ageos/http_proxy.h"

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
    if (rc != 0) {
        fprintf(stderr, "parse failed: rc=%d raw=%s\n", rc, raw);
        return 1;
    }
    if (strcmp(parsed.method, method) != 0) {
        fprintf(stderr, "method mismatch: got=%s want=%s\n", parsed.method, method);
        return 1;
    }
    if (strcmp(parsed.url, url) != 0) {
        fprintf(stderr, "url mismatch: got=%s want=%s\n", parsed.url, url);
        return 1;
    }
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
    if (rc != 0) {
        fprintf(stderr, "parse failed: rc=%d raw=%s\n", rc, raw);
        return 1;
    }
    if (strcmp(parsed.method, method) != 0 || strcmp(parsed.url, url) != 0 ||
        strcmp(parsed.host, host) != 0 || strcmp(parsed.path, path) != 0 ||
        parsed.port != port || parsed.is_connect != is_connect) {
        fprintf(
            stderr,
            "target mismatch: got method=%s url=%s host=%s path=%s port=%u connect=%d\n",
            parsed.method,
            parsed.url,
            parsed.host,
            parsed.path,
            parsed.port,
            parsed.is_connect);
        return 1;
    }
    return 0;
}

#ifdef __linux__
static int expect_denied_response(int client_fd) {
    char buffer[512];
    ssize_t received = read(client_fd, buffer, sizeof(buffer) - 1);
    if (received <= 0) {
        fprintf(stderr, "failed to read proxy response\n");
        return 1;
    }
    buffer[received] = '\0';
    if (strstr(buffer, "403 Forbidden") == NULL) {
        fprintf(stderr, "missing 403 Forbidden in response: %s\n", buffer);
        return 1;
    }
    if (strstr(buffer, "AgeOS proxy denied the request") == NULL) {
        fprintf(stderr, "missing deny body in response: %s\n", buffer);
        return 1;
    }
    return 0;
}

static int test_handle_client_denies_get(void) {
    int fds[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, fds) != 0) {
        fprintf(stderr, "socketpair failed\n");
        return 1;
    }

    const char *request = "GET http://example.com/proxy-test HTTP/1.1\r\n\r\n";
    if (write(fds[0], request, strlen(request)) < 0) {
        close(fds[0]);
        close(fds[1]);
        fprintf(stderr, "failed to write proxy request\n");
        return 1;
    }

    int rc = ageos_http_proxy_handle_client(fds[1]);
    close(fds[1]);
    if (rc != 0) {
        close(fds[0]);
        fprintf(stderr, "handle_client failed rc=%d\n", rc);
        return 1;
    }

    rc = expect_denied_response(fds[0]);
    close(fds[0]);
    return rc;
}

static int test_handle_client_uses_trusted_agent_id_not_env(void) {
    char state_template[] = "/tmp/ageos-http-proxy-policy-test-XXXXXX";
    char *state_dir = mkdtemp(state_template);
    if (state_dir == NULL) {
        fprintf(stderr, "failed to create access policy state dir\n");
        return 1;
    }
    setenv("AGEOS_STATE_DIR", state_dir, 1);

    ageos_access_request always;
    memset(&always, 0, sizeof(always));
    snprintf(always.kind, sizeof(always.kind), "http");
    snprintf(always.subject, sizeof(always.subject), "example.com");
    snprintf(always.method, sizeof(always.method), "GET");
    snprintf(always.path, sizeof(always.path), "/manifest-impersonation");
    if (ageos_access_apply_policy("agt-victim-manifest", &always, "always") != 0) {
        fprintf(stderr, "failed to apply victim always manifest\n");
        return 1;
    }
    setenv("AGEOS_AGENT_ID", "agt-victim-manifest", 1);

    int fds[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, fds) != 0) {
        fprintf(stderr, "socketpair failed\n");
        return 1;
    }
    const char *request = "GET http://example.com/manifest-impersonation HTTP/1.1\r\n\r\n";
    if (write(fds[0], request, strlen(request)) < 0) {
        close(fds[0]);
        close(fds[1]);
        fprintf(stderr, "failed to write proxy request\n");
        return 1;
    }

    int rc = ageos_http_proxy_handle_client_for_agent(fds[1], "agt-real-manifest");
    close(fds[1]);
    if (rc != 0) {
        close(fds[0]);
        fprintf(stderr, "handle_client failed rc=%d\n", rc);
        return 1;
    }
    rc = expect_denied_response(fds[0]);
    close(fds[0]);
    unsetenv("AGEOS_AGENT_ID");
    unsetenv("AGEOS_STATE_DIR");
    return rc;
}

static int test_broker_prompts_when_pending_write_would_fail(void) {
    char state_template[] = "/tmp/ageos-http-proxy-broker-test-XXXXXX";
    char *state_dir = mkdtemp(state_template);
    if (state_dir == NULL) {
        fprintf(stderr, "failed to create broker state dir\n");
        return 1;
    }
    setenv("AGEOS_STATE_DIR", state_dir, 1);
    char manifest_path[1024];
    if (ageos_access_manifest_path("agt-brokerwrite", manifest_path, sizeof(manifest_path)) != 0) {
        fprintf(stderr, "failed to create broker manifest path\n");
        return 1;
    }
    if (mkdir(manifest_path, 0700) != 0) {
        fprintf(stderr, "failed to make manifest path unwriteable\n");
        return 1;
    }

    int client_fds[2];
    int broker_fds[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, client_fds) != 0 || socketpair(AF_UNIX, SOCK_STREAM, 0, broker_fds) != 0) {
        fprintf(stderr, "socketpair failed for broker test\n");
        return 1;
    }
    const char *request = "GET http://google.com/ HTTP/1.1\r\nHost: google.com\r\n\r\n";
    if (write(client_fds[0], request, strlen(request)) < 0) {
        fprintf(stderr, "failed to write broker test request\n");
        return 1;
    }

    pid_t pid = fork();
    if (pid < 0) {
        fprintf(stderr, "fork failed for broker test\n");
        return 1;
    }
    if (pid == 0) {
        close(client_fds[0]);
        close(broker_fds[0]);
        int rc = ageos_http_proxy_handle_client_for_agent_broker(client_fds[1], "agt-brokerwrite", broker_fds[1]);
        close(client_fds[1]);
        close(broker_fds[1]);
        _exit(rc == 0 ? 0 : 2);
    }
    close(client_fds[1]);
    close(broker_fds[1]);

    fd_set reads;
    FD_ZERO(&reads);
    FD_SET(broker_fds[0], &reads);
    struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
    int ready = select(broker_fds[0] + 1, &reads, NULL, NULL, &timeout);
    if (ready <= 0) {
        fprintf(stderr, "broker was not prompted before pending write failure\n");
        kill(pid, SIGTERM);
        waitpid(pid, NULL, 0);
        return 1;
    }
    char broker_request[512];
    ssize_t received = read(broker_fds[0], broker_request, sizeof(broker_request) - 1);
    if (received <= 0) {
        fprintf(stderr, "failed to read broker request\n");
        return 1;
    }
    broker_request[received] = '\0';
    if (strstr(broker_request, "\"subject\":\"google.com\"") == NULL) {
        fprintf(stderr, "broker request missing google.com: %s\n", broker_request);
        return 1;
    }
    if (write(broker_fds[0], "deny\n", strlen("deny\n")) < 0) {
        fprintf(stderr, "failed to write broker denial\n");
        return 1;
    }
    int rc = expect_denied_response(client_fds[0]);
    close(client_fds[0]);
    close(broker_fds[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    unsetenv("AGEOS_STATE_DIR");
    if (rc != 0 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        fprintf(stderr, "broker test child failed status=%d\n", status);
        return 1;
    }
    return 0;
}

static int test_serve_rejects_invalid_listen_port(void) {
    if (ageos_http_proxy_serve(0U, -1) != -EINVAL) {
        fprintf(stderr, "expected EINVAL for port 0\n");
        return 1;
    }
    if (ageos_http_proxy_serve(65536U, -1) != -EINVAL) {
        fprintf(stderr, "expected EINVAL for port 65536\n");
        return 1;
    }
    if (ageos_http_proxy_serve(70000U, -1) != -EINVAL) {
        fprintf(stderr, "expected EINVAL for port 70000\n");
        return 1;
    }
    return 0;
}
#endif

int main(void) {
    if (expect_request(
            "GET http://example.com/path?q=1 HTTP/1.1\r\nHost: ignored.example\r\n\r\n",
            "GET",
            "http://example.com/path?q=1") != 0) {
        return 1;
    }
    if (expect_request_target(
            "GET http://Example.com:8080/path?q=1 HTTP/1.1\r\nHost: ignored.example\r\n\r\n",
            "GET",
            "http://Example.com:8080/path?q=1",
            "example.com",
            "/path?q=1",
            8080U,
            0) != 0) {
        return 1;
    }
    if (expect_request(
            "GET https://example.com/secure HTTP/1.1\r\nHost: example.com\r\n\r\n",
            "GET",
            "https://example.com/secure") != 0) {
        return 1;
    }
    if (expect_request(
            "CONNECT mcp.example.test:443 HTTP/1.1\r\nHost: mcp.example.test:443\r\n\r\n",
            "CONNECT",
            "mcp.example.test:443") != 0) {
        return 1;
    }
    if (expect_request_target(
            "CONNECT mcp.example.test:443 HTTP/1.1\r\nHost: mcp.example.test:443\r\n\r\n",
            "CONNECT",
            "mcp.example.test:443",
            "mcp.example.test",
            "",
            443U,
            1) != 0) {
        return 1;
    }
    if (expect_request(
            "POST /rpc HTTP/1.1\r\nHost: 127.0.0.1:9000\r\nContent-Length: 2\r\n\r\n{}",
            "POST",
            "http://127.0.0.1:9000/rpc") != 0) {
        return 1;
    }
    ageos_http_proxy_request parsed;
    if (ageos_http_proxy_parse_request("not-http\r\n", strlen("not-http\r\n"), &parsed) == 0) {
        fprintf(stderr, "invalid request unexpectedly parsed\n");
        return 1;
    }
#ifdef __linux__
    if (test_handle_client_denies_get() != 0) {
        return 1;
    }
    if (test_handle_client_uses_trusted_agent_id_not_env() != 0) {
        return 1;
    }
    if (test_broker_prompts_when_pending_write_would_fail() != 0) {
        return 1;
    }
    if (test_serve_rejects_invalid_listen_port() != 0) {
        return 1;
    }
#endif
    return 0;
}
