#define _GNU_SOURCE

#include "ageos/http_proxy.h"
#include "ageos/log.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#ifdef __linux__
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#endif

static int copy_token(char *dest, size_t dest_size, const char *start, size_t len) {
    if (dest == NULL || dest_size == 0) {
        return -EINVAL;
    }
    if (len >= dest_size) {
        len = dest_size - 1;
    }
    memcpy(dest, start, len);
    dest[len] = '\0';
    return 0;
}

static int copy_string(char *dest, size_t dest_size, const char *value) {
    return copy_token(dest, dest_size, value, strlen(value));
}

static const char *find_line_end(const char *data, const char *end) {
    for (const char *cursor = data; cursor < end; cursor++) {
        if (*cursor == '\n') {
            return cursor;
        }
    }
    return NULL;
}

static const char *trim_line_end(const char *line, const char *line_end) {
    while (line_end > line && (line_end[-1] == '\r' || line_end[-1] == '\n')) {
        line_end--;
    }
    return line_end;
}

static const char *skip_spaces(const char *cursor, const char *end) {
    while (cursor < end && (*cursor == ' ' || *cursor == '\t')) {
        cursor++;
    }
    return cursor;
}

static const char *find_space(const char *cursor, const char *end) {
    while (cursor < end && *cursor != ' ' && *cursor != '\t') {
        cursor++;
    }
    return cursor;
}

static int starts_with_scheme(const char *target, size_t target_len) {
    return (target_len >= 7 && strncasecmp(target, "http://", 7) == 0) ||
           (target_len >= 8 && strncasecmp(target, "https://", 8) == 0);
}

static const char *find_header_value(
    const char *headers,
    const char *end,
    const char *name,
    size_t *value_len) {
    size_t name_len = strlen(name);
    const char *cursor = headers;
    while (cursor < end) {
        const char *line_end = find_line_end(cursor, end);
        if (line_end == NULL) {
            line_end = end;
        }
        const char *trimmed_end = trim_line_end(cursor, line_end);
        if ((size_t)(trimmed_end - cursor) > name_len &&
            strncasecmp(cursor, name, name_len) == 0 &&
            cursor[name_len] == ':') {
            const char *value = skip_spaces(cursor + name_len + 1, trimmed_end);
            *value_len = (size_t)(trimmed_end - value);
            return value;
        }
        cursor = line_end < end ? line_end + 1 : end;
    }
    return NULL;
}

static int build_origin_url(
    const char *target,
    size_t target_len,
    const char *host,
    size_t host_len,
    char *buffer,
    size_t buffer_size) {
    if (host == NULL || host_len == 0 || target_len == 0 || target[0] != '/') {
        return copy_token(buffer, buffer_size, target, target_len);
    }
    int written = snprintf(buffer, buffer_size, "http://%.*s%.*s", (int)host_len, host, (int)target_len, target);
    return (written < 0 || (size_t)written >= buffer_size) ? -ENAMETOOLONG : 0;
}

int ageos_http_proxy_parse_request(const char *request, size_t request_len, ageos_http_proxy_request *parsed) {
    if (request == NULL || request_len == 0 || parsed == NULL) {
        return -EINVAL;
    }
    memset(parsed, 0, sizeof(*parsed));
    const char *end = request + request_len;
    const char *line_end = find_line_end(request, end);
    if (line_end == NULL) {
        return -EINVAL;
    }
    line_end = trim_line_end(request, line_end);

    const char *method_start = request;
    const char *method_end = find_space(method_start, line_end);
    const char *target_start = skip_spaces(method_end, line_end);
    const char *target_end = find_space(target_start, line_end);
    const char *version_start = skip_spaces(target_end, line_end);
    if (method_start == method_end || target_start == target_end || version_start == line_end) {
        return -EINVAL;
    }
    if (copy_token(parsed->method, sizeof(parsed->method), method_start, (size_t)(method_end - method_start)) != 0) {
        return -EINVAL;
    }

    size_t target_len = (size_t)(target_end - target_start);
    if (strcasecmp(parsed->method, "CONNECT") == 0 || starts_with_scheme(target_start, target_len)) {
        return copy_token(parsed->url, sizeof(parsed->url), target_start, target_len);
    }

    const char *headers_start = line_end;
    while (headers_start < end && (*headers_start == '\r' || *headers_start == '\n')) {
        headers_start++;
    }
    size_t host_len = 0;
    const char *host = find_header_value(headers_start, end, "Host", &host_len);
    return build_origin_url(target_start, target_len, host, host_len, parsed->url, sizeof(parsed->url));
}

#ifdef __linux__
static int write_all(int fd, const char *data, size_t len) {
    size_t offset = 0;
    while (offset < len) {
        ssize_t written = write(fd, data + offset, len - offset);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -errno;
        }
        if (written == 0) {
            return -EPIPE;
        }
        offset += (size_t)written;
    }
    return 0;
}

static int create_loopback_listener(uint32_t port) {
    if (!ageos_tcp_port_is_valid(port)) {
        return -EINVAL;
    }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -errno;
    }
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((uint16_t)port);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        int err = errno;
        close(fd);
        return -err;
    }
    if (listen(fd, 64) != 0) {
        int err = errno;
        close(fd);
        return -err;
    }
    return fd;
}

int ageos_http_proxy_handle_client(int client_fd) {
    char buffer[8192];
    ssize_t received;
    do {
        received = recv(client_fd, buffer, sizeof(buffer) - 1, 0);
    } while (received < 0 && errno == EINTR);
    if (received <= 0) {
        return received == 0 ? 0 : -errno;
    }
    buffer[received] = '\0';

    ageos_http_proxy_request parsed;
    int parse_rc = ageos_http_proxy_parse_request(buffer, (size_t)received, &parsed);
    if (parse_rc != 0) {
        copy_string(parsed.method, sizeof(parsed.method), "UNKNOWN");
        copy_string(parsed.url, sizeof(parsed.url), "<unparseable>");
    }
    AGEOS_LOG_INFO("http proxy denied", "method=%s url=%s", parsed.method, parsed.url);

    static const char response[] =
        "HTTP/1.1 403 Forbidden\r\n"
        "Connection: close\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 31\r\n"
        "\r\n"
        "AgeOS proxy denied the request\n";
    return write_all(client_fd, response, sizeof(response) - 1);
}

static int signal_proxy_ready(int ready_fd) {
    if (ready_fd < 0) {
        return 0;
    }
    char byte = 1;
    ssize_t written = write(ready_fd, &byte, 1);
    if (written < 0) {
        return -errno;
    }
    if (written == 0) {
        return -EPIPE;
    }
    return 0;
}

int ageos_http_proxy_serve(uint32_t listen_port, int ready_fd) {
    int listener_fd = create_loopback_listener(listen_port);
    if (listener_fd < 0) {
        AGEOS_LOG_ERROR("failed to expose http proxy endpoint", "%s", strerror(-listener_fd));
        return listener_fd;
    }
    AGEOS_LOG_INFO("started http deny proxy", "port=%u", listen_port);
    int ready_rc = signal_proxy_ready(ready_fd);
    if (ready_rc != 0) {
        close(listener_fd);
        return ready_rc;
    }
    if (ready_fd >= 0) {
        close(ready_fd);
    }
    for (;;) {
        int client_fd = accept(listener_fd, NULL, NULL);
        if (client_fd < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        ageos_http_proxy_handle_client(client_fd);
        close(client_fd);
    }
    close(listener_fd);
    return 0;
}
#else
int ageos_http_proxy_handle_client(int client_fd) {
    (void)client_fd;
    return -ENOTSUP;
}

int ageos_http_proxy_serve(uint32_t listen_port, int ready_fd) {
    (void)listen_port;
    (void)ready_fd;
    return -ENOTSUP;
}
#endif
