#pragma once

#include <stddef.h>
#include <stdint.h>

#define BUBBLEHUB_HTTP_PROXY_DEFAULT_PORT 18080U
#define BUBBLEHUB_HTTP_PROXY_METHOD_SIZE 16U
#define BUBBLEHUB_HTTP_PROXY_URL_SIZE 2048U
#define BUBBLEHUB_HTTP_PROXY_HOST_SIZE 256U
#define BUBBLEHUB_HTTP_PROXY_PATH_SIZE 1024U
#define BUBBLEHUB_TCP_PORT_MAX 65535U

static inline int bubblehub_tcp_port_is_valid(uint32_t port) {
    return port > 0U && port <= BUBBLEHUB_TCP_PORT_MAX;
}

typedef struct {
    char method[BUBBLEHUB_HTTP_PROXY_METHOD_SIZE];
    char url[BUBBLEHUB_HTTP_PROXY_URL_SIZE];
    char host[BUBBLEHUB_HTTP_PROXY_HOST_SIZE];
    char path[BUBBLEHUB_HTTP_PROXY_PATH_SIZE];
    uint32_t port;
    int is_connect;
} bubblehub_http_proxy_request;

int bubblehub_http_proxy_parse_request(const char *request, size_t request_len, bubblehub_http_proxy_request *parsed);
int bubblehub_http_proxy_handle_client(int client_fd);
int bubblehub_http_proxy_handle_client_for_agent(int client_fd, const char *agent_id);
int bubblehub_http_proxy_handle_client_for_agent_broker(int client_fd, const char *agent_id, int access_broker_fd);
int bubblehub_http_proxy_serve(uint32_t listen_port, int ready_fd);
