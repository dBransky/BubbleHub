#include "ageos/http_proxy.h"

#include <stdio.h>
#include <string.h>

#ifdef __linux__
#include <errno.h>
#include <sys/socket.h>
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
    if (test_serve_rejects_invalid_listen_port() != 0) {
        return 1;
    }
#endif
    return 0;
}
