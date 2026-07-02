#define _GNU_SOURCE

#include "bubblehub/http_proxy.h"

#include "bubblehub/access_policy.h"
#include "bubblehub/log.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#ifdef __linux__
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/select.h>
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

static void lowercase_ascii(char *value) {
    for (; value != NULL && *value != '\0'; value++) {
        if (*value >= 'A' && *value <= 'Z') {
            *value = (char)(*value - 'A' + 'a');
        }
    }
}

static uint32_t default_port_for_scheme(const char *scheme, size_t scheme_len) {
    if (scheme_len == 5 && strncasecmp(scheme, "https", 5) == 0) {
        return 443U;
    }
    return 80U;
}

static int split_host_port(
    const char *value,
    size_t value_len,
    uint32_t default_port,
    char *host,
    size_t host_size,
    uint32_t *port) {
    if (value == NULL || value_len == 0 || host == NULL || port == NULL) {
        return -EINVAL;
    }
    const char *host_start = value;
    const char *host_end = value + value_len;
    const char *port_start = NULL;
    if (*host_start == '[') {
        const char *closing = memchr(host_start, ']', value_len);
        if (closing != NULL) {
            host_start++;
            host_end = closing;
            if (closing + 1 < value + value_len && closing[1] == ':') {
                port_start = closing + 2;
            }
        }
    } else {
        const char *colon = NULL;
        for (const char *cursor = value; cursor < value + value_len; cursor++) {
            if (*cursor == ':') {
                if (colon != NULL) {
                    colon = NULL;
                    break;
                }
                colon = cursor;
            }
        }
        if (colon != NULL) {
            host_end = colon;
            port_start = colon + 1;
        }
    }
    if (host_end <= host_start) {
        return -EINVAL;
    }
    if (copy_token(host, host_size, host_start, (size_t)(host_end - host_start)) != 0) {
        return -EINVAL;
    }
    lowercase_ascii(host);
    *port = default_port;
    if (port_start != NULL && port_start < value + value_len) {
        char port_buf[16];
        size_t port_len = (size_t)(value + value_len - port_start);
        if (port_len >= sizeof(port_buf)) {
            return -EINVAL;
        }
        memcpy(port_buf, port_start, port_len);
        port_buf[port_len] = '\0';
        char *end = NULL;
        unsigned long parsed = strtoul(port_buf, &end, 10);
        if (end == port_buf || *end != '\0' || !bubblehub_tcp_port_is_valid((uint32_t)parsed)) {
            return -EINVAL;
        }
        *port = (uint32_t)parsed;
    }
    return 0;
}

static int parse_absolute_url(bubblehub_http_proxy_request *parsed, const char *target, size_t target_len) {
    const char *scheme_end = memchr(target, ':', target_len);
    if (scheme_end == NULL || scheme_end + 2 >= target + target_len || scheme_end[1] != '/' || scheme_end[2] != '/') {
        return -EINVAL;
    }
    const char *authority = scheme_end + 3;
    const char *path = memchr(authority, '/', (size_t)(target + target_len - authority));
    const char *authority_end = path != NULL ? path : target + target_len;
    uint32_t default_port = default_port_for_scheme(target, (size_t)(scheme_end - target));
    int rc = split_host_port(
        authority,
        (size_t)(authority_end - authority),
        default_port,
        parsed->host,
        sizeof(parsed->host),
        &parsed->port);
    if (rc != 0) {
        return rc;
    }
    if (path != NULL) {
        return copy_token(parsed->path, sizeof(parsed->path), path, (size_t)(target + target_len - path));
    }
    return copy_string(parsed->path, sizeof(parsed->path), "/");
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

int bubblehub_http_proxy_parse_request(const char *request, size_t request_len, bubblehub_http_proxy_request *parsed) {
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
        int rc = copy_token(parsed->url, sizeof(parsed->url), target_start, target_len);
        if (rc != 0) {
            return rc;
        }
        if (strcasecmp(parsed->method, "CONNECT") == 0) {
            parsed->is_connect = 1;
            rc = split_host_port(
                target_start,
                target_len,
                443U,
                parsed->host,
                sizeof(parsed->host),
                &parsed->port);
            parsed->path[0] = '\0';
            return rc;
        }
        return parse_absolute_url(parsed, target_start, target_len);
    }

    const char *headers_start = line_end;
    while (headers_start < end && (*headers_start == '\r' || *headers_start == '\n')) {
        headers_start++;
    }
    size_t host_len = 0;
    const char *host = find_header_value(headers_start, end, "Host", &host_len);
    int rc = build_origin_url(target_start, target_len, host, host_len, parsed->url, sizeof(parsed->url));
    if (rc != 0) {
        return rc;
    }
    rc = split_host_port(host, host_len, 80U, parsed->host, sizeof(parsed->host), &parsed->port);
    if (rc != 0) {
        return rc;
    }
    return copy_token(parsed->path, sizeof(parsed->path), target_start, target_len);
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

static void proxy_loop(int left_fd, int right_fd) {
    char buffer[65536];
    for (;;) {
        fd_set reads;
        FD_ZERO(&reads);
        FD_SET(left_fd, &reads);
        FD_SET(right_fd, &reads);
        int max_fd = left_fd > right_fd ? left_fd : right_fd;
        int ready = select(max_fd + 1, &reads, NULL, NULL, NULL);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            return;
        }
        int source = FD_ISSET(left_fd, &reads) ? left_fd : right_fd;
        int target = source == left_fd ? right_fd : left_fd;
        ssize_t read_count = read(source, buffer, sizeof(buffer));
        if (read_count <= 0) {
            return;
        }
        if (write_all(target, buffer, (size_t)read_count) != 0) {
            return;
        }
    }
}

static int connect_to_upstream(const char *host, uint32_t port) {
    if (host == NULL || host[0] == '\0' || !bubblehub_tcp_port_is_valid(port)) {
        return -EINVAL;
    }
    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%u", port);
    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    struct addrinfo *results = NULL;
    int gai = getaddrinfo(host, port_str, &hints, &results);
    if (gai != 0) {
        return -EHOSTUNREACH;
    }
    int fd = -1;
    for (struct addrinfo *item = results; item != NULL; item = item->ai_next) {
        fd = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
        if (fd < 0) {
            continue;
        }
        if (connect(fd, item->ai_addr, item->ai_addrlen) == 0) {
            break;
        }
        close(fd);
        fd = -1;
    }
    freeaddrinfo(results);
    return fd >= 0 ? fd : -ECONNREFUSED;
}

static int write_denied_response(int client_fd) {
    static const char body[] =
        "BubbleHub proxy denied the request\n"
        "Run `bubblehub dashboard` to review pending access requests.\n";
    char header[256];
    int written = snprintf(
        header,
        sizeof(header),
        "HTTP/1.1 403 Forbidden\r\n"
        "Connection: close\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: %zu\r\n"
        "\r\n",
        sizeof(body) - 1);
    if (written < 0 || (size_t)written >= sizeof(header)) {
        return -ENOSPC;
    }
    int rc = write_all(client_fd, header, (size_t)written);
    if (rc != 0) {
        return rc;
    }
    return write_all(client_fd, body, sizeof(body) - 1);
}

static int policy_request_from_http(const bubblehub_http_proxy_request *parsed, bubblehub_access_request *request) {
    memset(request, 0, sizeof(*request));
    snprintf(request->kind, sizeof(request->kind), "http");
    snprintf(request->subject, sizeof(request->subject), "%s", parsed->host);
    snprintf(request->method, sizeof(request->method), "%s", parsed->method);
    snprintf(request->path, sizeof(request->path), "%.*s", (int)sizeof(request->path) - 1, parsed->path);
    return parsed->host[0] == '\0' ? -EINVAL : 0;
}

static int append_text(char *buffer, size_t buffer_size, size_t *offset, const char *value) {
    size_t len = strlen(value);
    if (*offset + len >= buffer_size) {
        return -ENOSPC;
    }
    memcpy(buffer + *offset, value, len);
    *offset += len;
    buffer[*offset] = '\0';
    return 0;
}

static int append_json_string(char *buffer, size_t buffer_size, size_t *offset, const char *value) {
    if (append_text(buffer, buffer_size, offset, "\"") != 0) {
        return -ENOSPC;
    }
    for (const unsigned char *cursor = (const unsigned char *)value; cursor != NULL && *cursor != '\0'; cursor++) {
        char escaped[7];
        switch (*cursor) {
            case '"':
                if (append_text(buffer, buffer_size, offset, "\\\"") != 0) {
                    return -ENOSPC;
                }
                break;
            case '\\':
                if (append_text(buffer, buffer_size, offset, "\\\\") != 0) {
                    return -ENOSPC;
                }
                break;
            case '\b':
                if (append_text(buffer, buffer_size, offset, "\\b") != 0) {
                    return -ENOSPC;
                }
                break;
            case '\f':
                if (append_text(buffer, buffer_size, offset, "\\f") != 0) {
                    return -ENOSPC;
                }
                break;
            case '\n':
                if (append_text(buffer, buffer_size, offset, "\\n") != 0) {
                    return -ENOSPC;
                }
                break;
            case '\r':
                if (append_text(buffer, buffer_size, offset, "\\r") != 0) {
                    return -ENOSPC;
                }
                break;
            case '\t':
                if (append_text(buffer, buffer_size, offset, "\\t") != 0) {
                    return -ENOSPC;
                }
                break;
            default:
                if (*cursor < 0x20) {
                    snprintf(escaped, sizeof(escaped), "\\u%04x", *cursor);
                    if (append_text(buffer, buffer_size, offset, escaped) != 0) {
                        return -ENOSPC;
                    }
                } else {
                    if (*offset + 1 >= buffer_size) {
                        return -ENOSPC;
                    }
                    buffer[(*offset)++] = (char)*cursor;
                    buffer[*offset] = '\0';
                }
                break;
        }
    }
    return append_text(buffer, buffer_size, offset, "\"");
}

static int append_json_field(char *buffer, size_t buffer_size, size_t *offset, const char *name, const char *value, int first) {
    if (!first && append_text(buffer, buffer_size, offset, ",") != 0) {
        return -ENOSPC;
    }
    if (append_json_string(buffer, buffer_size, offset, name) != 0 ||
        append_text(buffer, buffer_size, offset, ":") != 0 ||
        append_json_string(buffer, buffer_size, offset, value) != 0) {
        return -ENOSPC;
    }
    return 0;
}

static int read_broker_response(int access_broker_fd, char *response, size_t response_size) {
    if (response == NULL || response_size == 0) {
        return -EINVAL;
    }
    size_t offset = 0;
    for (;;) {
        char byte = '\0';
        ssize_t received = read(access_broker_fd, &byte, 1);
        if (received < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -errno;
        }
        if (received == 0) {
            return -EPIPE;
        }
        if (byte == '\n') {
            response[offset] = '\0';
            return 0;
        }
        if (byte == '\r') {
            continue;
        }
        if (offset + 1 >= response_size) {
            return -ENOSPC;
        }
        response[offset++] = byte;
    }
}

static int request_broker_decision(
    int access_broker_fd,
    const char *agent_id,
    const bubblehub_access_request *request,
    bubblehub_access_decision *decision) {
    if (access_broker_fd < 0 || agent_id == NULL || request == NULL || decision == NULL) {
        return -EINVAL;
    }
    char payload[1400];
    size_t offset = 0;
    if (append_text(payload, sizeof(payload), &offset, "{") != 0 ||
        append_json_field(payload, sizeof(payload), &offset, "agent_id", agent_id, 1) != 0 ||
        append_json_field(payload, sizeof(payload), &offset, "kind", request->kind, 0) != 0 ||
        append_json_field(payload, sizeof(payload), &offset, "subject", request->subject, 0) != 0 ||
        append_json_field(payload, sizeof(payload), &offset, "method", request->method, 0) != 0 ||
        append_json_field(payload, sizeof(payload), &offset, "path", request->path, 0) != 0 ||
        append_text(payload, sizeof(payload), &offset, "}\n") != 0) {
        return -ENOSPC;
    }
    int rc = write_all(access_broker_fd, payload, offset);
    if (rc != 0) {
        return rc;
    }
    char response[32];
    rc = read_broker_response(access_broker_fd, response, sizeof(response));
    if (rc != 0) {
        return rc;
    }
    if (strcmp(response, "always") == 0 || strcmp(response, "approve") == 0 || strcmp(response, "ask") == 0) {
        *decision = BUBBLEHUB_ACCESS_DECISION_APPROVE;
        return 0;
    }
    if (strcmp(response, "never") == 0 || strcmp(response, "deny") == 0) {
        *decision = BUBBLEHUB_ACCESS_DECISION_DENY;
        return 0;
    }
    return -EINVAL;
}

static int create_loopback_listener(uint32_t port) {
    if (!bubblehub_tcp_port_is_valid(port)) {
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

int bubblehub_http_proxy_handle_client_for_agent_broker(int client_fd, const char *agent_id, int access_broker_fd) {
    char buffer[8192];
    ssize_t received;
    do {
        received = recv(client_fd, buffer, sizeof(buffer) - 1, 0);
    } while (received < 0 && errno == EINTR);
    if (received <= 0) {
        return received == 0 ? 0 : -errno;
    }
    buffer[received] = '\0';

    bubblehub_http_proxy_request parsed;
    int parse_rc = bubblehub_http_proxy_parse_request(buffer, (size_t)received, &parsed);
    if (parse_rc != 0) {
        copy_string(parsed.method, sizeof(parsed.method), "UNKNOWN");
        copy_string(parsed.url, sizeof(parsed.url), "<unparseable>");
    }
    if (parse_rc != 0 || agent_id == NULL || agent_id[0] == '\0') {
        BUBBLEHUB_LOG_INFO("http proxy denied", "method=%s url=%s", parsed.method, parsed.url);
        return write_denied_response(client_fd);
    }

    bubblehub_access_request request;
    if (policy_request_from_http(&parsed, &request) != 0) {
        BUBBLEHUB_LOG_INFO("http proxy denied", "method=%s url=%s", parsed.method, parsed.url);
        return write_denied_response(client_fd);
    }
    int needs_prompt = 0;
    bubblehub_access_decision decision = BUBBLEHUB_ACCESS_DECISION_DENY;
    if (access_broker_fd >= 0 && bubblehub_access_needs_prompt(agent_id, &request, &needs_prompt) == 0 && needs_prompt) {
        int broker_rc = request_broker_decision(access_broker_fd, agent_id, &request, &decision);
        if (broker_rc != 0) {
            BUBBLEHUB_LOG_INFO("http proxy access broker failed", "method=%s url=%s err=%s", parsed.method, parsed.url, strerror(-broker_rc));
        }
    } else {
        int policy_rc = bubblehub_access_evaluate(agent_id, &request, 0, &decision);
        if (policy_rc != 0) {
            BUBBLEHUB_LOG_INFO("http proxy access policy failed", "method=%s url=%s err=%s", parsed.method, parsed.url, strerror(-policy_rc));
            decision = BUBBLEHUB_ACCESS_DECISION_DENY;
        }
    }
    if (decision != BUBBLEHUB_ACCESS_DECISION_APPROVE) {
        BUBBLEHUB_LOG_INFO("http proxy denied", "method=%s url=%s", parsed.method, parsed.url);
        return write_denied_response(client_fd);
    }

    int upstream_fd = connect_to_upstream(parsed.host, parsed.port);
    if (upstream_fd < 0) {
        BUBBLEHUB_LOG_INFO("http proxy upstream failed", "method=%s url=%s err=%s", parsed.method, parsed.url, strerror(-upstream_fd));
        return write_denied_response(client_fd);
    }
    BUBBLEHUB_LOG_INFO("http proxy approved", "method=%s url=%s", parsed.method, parsed.url);
    if (parsed.is_connect) {
        static const char connected[] = "HTTP/1.1 200 Connection Established\r\nConnection: close\r\n\r\n";
        int rc = write_all(client_fd, connected, sizeof(connected) - 1);
        if (rc == 0) {
            proxy_loop(client_fd, upstream_fd);
        }
        close(upstream_fd);
        return rc;
    }
    int rc = write_all(upstream_fd, buffer, (size_t)received);
    if (rc == 0) {
        proxy_loop(client_fd, upstream_fd);
    }
    close(upstream_fd);
    return rc;
}

int bubblehub_http_proxy_handle_client_for_agent(int client_fd, const char *agent_id) {
    return bubblehub_http_proxy_handle_client_for_agent_broker(client_fd, agent_id, -1);
}

int bubblehub_http_proxy_handle_client(int client_fd) {
    return bubblehub_http_proxy_handle_client_for_agent(client_fd, NULL);
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

int bubblehub_http_proxy_serve(uint32_t listen_port, int ready_fd) {
    int listener_fd = create_loopback_listener(listen_port);
    if (listener_fd < 0) {
        BUBBLEHUB_LOG_ERROR("failed to expose http proxy endpoint", "%s", strerror(-listener_fd));
        return listener_fd;
    }
    BUBBLEHUB_LOG_INFO("started http deny proxy", "port=%u", listen_port);
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
        bubblehub_http_proxy_handle_client(client_fd);
        close(client_fd);
    }
    close(listener_fd);
    return 0;
}
#else
int bubblehub_http_proxy_handle_client(int client_fd) {
    (void)client_fd;
    return -ENOTSUP;
}

int bubblehub_http_proxy_serve(uint32_t listen_port, int ready_fd) {
    (void)listen_port;
    (void)ready_fd;
    return -ENOTSUP;
}
#endif
