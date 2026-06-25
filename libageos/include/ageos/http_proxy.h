#pragma once

#include <stddef.h>
#include <stdint.h>

#define AGEOS_HTTP_PROXY_DEFAULT_PORT 18080U
#define AGEOS_HTTP_PROXY_METHOD_SIZE 16U
#define AGEOS_HTTP_PROXY_URL_SIZE 2048U
#define AGEOS_TCP_PORT_MAX 65535U

static inline int ageos_tcp_port_is_valid(uint32_t port) {
    return port > 0U && port <= AGEOS_TCP_PORT_MAX;
}

typedef struct {
    char method[AGEOS_HTTP_PROXY_METHOD_SIZE];
    char url[AGEOS_HTTP_PROXY_URL_SIZE];
} ageos_http_proxy_request;

int ageos_http_proxy_parse_request(const char *request, size_t request_len, ageos_http_proxy_request *parsed);
int ageos_http_proxy_handle_client(int client_fd);
int ageos_http_proxy_serve(uint32_t listen_port, int ready_fd);
