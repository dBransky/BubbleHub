#pragma once

#include <stddef.h>
#include <stdint.h>

#define AGEOS_HTTP_PROXY_DEFAULT_PORT 18080U
#define AGEOS_HTTP_PROXY_METHOD_SIZE 16U
#define AGEOS_HTTP_PROXY_URL_SIZE 2048U
#define AGEOS_HTTP_PROXY_HOST_SIZE 256U
#define AGEOS_HTTP_PROXY_PATH_SIZE 1024U
#define AGEOS_TCP_PORT_MAX 65535U

static inline int ageos_tcp_port_is_valid(uint32_t port) {
    return port > 0U && port <= AGEOS_TCP_PORT_MAX;
}

typedef struct {
    char method[AGEOS_HTTP_PROXY_METHOD_SIZE];
    char url[AGEOS_HTTP_PROXY_URL_SIZE];
    char host[AGEOS_HTTP_PROXY_HOST_SIZE];
    char path[AGEOS_HTTP_PROXY_PATH_SIZE];
    uint32_t port;
    int is_connect;
} ageos_http_proxy_request;

int ageos_http_proxy_parse_request(const char *request, size_t request_len, ageos_http_proxy_request *parsed);
int ageos_http_proxy_handle_client(int client_fd);
int ageos_http_proxy_handle_client_for_agent(int client_fd, const char *agent_id);
int ageos_http_proxy_handle_client_for_agent_broker(int client_fd, const char *agent_id, int access_broker_fd);
int ageos_http_proxy_serve(uint32_t listen_port, int ready_fd);
