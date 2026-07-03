#define _GNU_SOURCE

#include "bubblehub/limits.h"
#include "bubblehub/log.h"
#include "bubblehub/overfs.h"
#include "bubblehub/sandbox.h"

#include "test_common.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

static int test_mkdir_p_impl(const char *path, mode_t mode) {
    if (path == NULL || path[0] == '\0') {
        return -EINVAL;
    }
    char buffer[1024];
    int written = snprintf(buffer, sizeof(buffer), "%s", path);
    if (written < 0 || (size_t)written >= sizeof(buffer)) {
        return -ENAMETOOLONG;
    }
    for (char *cursor = buffer + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        if (mkdir(buffer, mode) != 0 && errno != EEXIST) {
            return -errno;
        }
        *cursor = '/';
    }
    if (mkdir(buffer, mode) != 0 && errno != EEXIST) {
        return -errno;
    }
    return 0;
}

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

int bubblehub_apply_cgroup_limits(const bubblehub_sandbox_config *cfg) {
    (void)cfg;
    return 0;
}

int bubblehub_landlock_apply_filesystem(const char *writable_dir, int allow_dns) {
    (void)writable_dir;
    (void)allow_dns;
    return 0;
}

int bubblehub_http_proxy_handle_client_for_agent(int client_fd, const char *agent_id) {
    (void)agent_id;
    close(client_fd);
    return 0;
}

int bubblehub_http_proxy_handle_client_for_agent_broker(int client_fd, const char *agent_id, int access_broker_fd) {
    (void)agent_id;
    (void)access_broker_fd;
    close(client_fd);
    return 0;
}

int bubblehub_overfs_rootfs_enabled(const bubblehub_sandbox_config *cfg) {
    return cfg != NULL && cfg->rootfs_dir != NULL && cfg->rootfs_dir[0] != '\0';
}

int bubblehub_overfs_setup_mounts(const char *new_root, const bubblehub_sandbox_config *cfg) {
    (void)new_root;
    (void)cfg;
    return 0;
}

int bubblehub_overfs_join_mount_path(const char *root, const char *path, char *buffer, size_t buffer_size) {
    if (path == NULL || path[0] != '/') {
        return -EINVAL;
    }
    int written = root == NULL || root[0] == '\0' ? snprintf(buffer, buffer_size, "%s", path) : snprintf(buffer, buffer_size, "%s%s", root, path);
    return written < 0 || (size_t)written >= buffer_size ? -ENAMETOOLONG : 0;
}

int bubblehub_overfs_mkdir_p(const char *path, mode_t mode) {
    return test_mkdir_p_impl(path, mode);
}

int bubblehub_overfs_ensure_file(const char *path, mode_t mode) {
    char parent[1024];
    int written = snprintf(parent, sizeof(parent), "%s", path);
    if (written < 0 || (size_t)written >= sizeof(parent)) {
        return -ENAMETOOLONG;
    }
    char *slash = strrchr(parent, '/');
    if (slash != NULL) {
        *slash = '\0';
        int rc = test_mkdir_p_impl(parent, 0755);
        if (rc != 0) {
            return rc;
        }
    }
    int fd = open(path, O_CREAT | O_RDWR | O_CLOEXEC, mode);
    if (fd < 0) {
        return -errno;
    }
    close(fd);
    return 0;
}

int bubblehub_overfs_bind_file_readonly(const char *source, const char *target) {
    (void)source;
    return bubblehub_overfs_ensure_file(target, 0644);
}

int bubblehub_overfs_bind_file_readwrite(const char *source, const char *target) {
    (void)source;
    return bubblehub_overfs_ensure_file(target, 0600);
}

int bubblehub_overfs_bind_dir(const char *source, const char *target) {
    (void)source;
    return test_mkdir_p_impl(target, 0700);
}

int bubblehub_overfs_bind_optional_dir_readonly(const char *source, const char *target) {
    (void)source;
    return test_mkdir_p_impl(target, 0755);
}

int bubblehub_overfs_bind_optional_file_readonly(const char *source, const char *target) {
    (void)source;
    return bubblehub_overfs_ensure_file(target, 0644);
}

int bubblehub_overfs_mount_tmpfs_at(const char *target, const char *options) {
    (void)options;
    return test_mkdir_p_impl(target, 0755);
}

#include "../sandbox.c"

static int test_prompt_and_agent_sanitizers(void) {
    char label[64];
    sanitize_prompt_label(NULL, label, sizeof(label));
    TEST_CHECK_STR(label, "BubbleHub");
    sanitize_prompt_label("review/agent\nprod", label, sizeof(label));
    TEST_CHECK_STR(label, "BubbleHub reviewagentprod");
    sanitize_prompt_label("!!!", label, sizeof(label));
    TEST_CHECK_STR(label, "BubbleHub");

    char agent[64];
    sanitize_agent_name(NULL, agent, sizeof(agent));
    TEST_CHECK_STR(agent, "agent");
    sanitize_agent_name("agt/test.name", agent, sizeof(agent));
    TEST_CHECK_STR(agent, "agt_test_name");
    return 0;
}

static int test_file_and_path_helpers(void) {
    char *dir = test_mkdtemp_copy("bubblehub-sandbox-internal-path");
    TEST_CHECK(dir != NULL);

    char path[512];
    snprintf(path, sizeof(path), "%s/nested/file.txt", dir);
    char nested_dir[512];
    snprintf(nested_dir, sizeof(nested_dir), "%s/nested", dir);
    TEST_CHECK(write_text_file(path, "hello", 0644) < 0);
    TEST_CHECK(test_mkdir_p_impl(nested_dir, 0755) == 0);
    TEST_CHECK(write_text_file(path, "hello", 0644) == 0);

    char buffer[64];
    size_t len = 0;
    TEST_CHECK(read_text_file_limited(path, buffer, sizeof(buffer), &len) == 0);
    TEST_CHECK_EQ(len, (size_t)5);
    TEST_CHECK_STR(buffer, "hello");
    TEST_CHECK(read_text_file_limited(path, buffer, 0, &len) == -EINVAL);
    TEST_CHECK(write_text_file_if_missing(path, "ignored", 0644) == 0);

    char parent[512];
    TEST_CHECK(parent_dir("/tmp/example/file", parent, sizeof(parent)) == 0);
    TEST_CHECK_STR(parent, "/tmp/example");
    TEST_CHECK(parent_dir("relative", parent, sizeof(parent)) == -EINVAL);

    char relative[512];
    TEST_CHECK(relative_workdir("/workspace/src", dir, relative, sizeof(relative)) == 0);
    TEST_CHECK_STR(relative, "src");
    TEST_CHECK(relative_workdir("/other", dir, relative, sizeof(relative)) == -EINVAL);
    TEST_CHECK(relative_workdir(dir, dir, relative, sizeof(relative)) == 0);
    TEST_CHECK_STR(relative, "");

    free(dir);
    return 0;
}

static int test_proxy_env_helpers(void) {
    unsetenv("NO_PROXY");
    unsetenv("no_proxy");
    unsetenv("HTTP_PROXY");
    TEST_CHECK(!no_proxy_has_entry(NULL, "localhost"));
    TEST_CHECK(!no_proxy_has_entry("example.com", ""));
    TEST_CHECK(no_proxy_has_entry(" example.com, localhost ", "localhost"));

    append_no_proxy_env("NO_PROXY", "127.0.0.1");
    append_no_proxy_env("NO_PROXY", "localhost");
    append_no_proxy_env("NO_PROXY", "localhost");
    TEST_CHECK_STR(getenv("NO_PROXY"), "127.0.0.1,localhost");

    apply_sandbox_http_proxy_env(18080);
    TEST_CHECK_STR(getenv("HTTP_PROXY"), "http://127.0.0.1:18080");
    TEST_CHECK_STR(getenv("BUBBLEHUB_HTTP_PROXY_PORT"), "18080");
    apply_sandbox_http_proxy_env(0);
    TEST_CHECK_STR(getenv("HTTP_PROXY"), "http://127.0.0.1:18080");
    return 0;
}

static int test_fd_and_sync_helpers(void) {
    int pair[2];
    TEST_CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
    int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    TEST_CHECK(fd >= 0);
    TEST_CHECK(send_fd(pair[0], fd) == 0);
    int received = recv_fd(pair[1]);
    TEST_CHECK(received >= 0);
    close(fd);
    close(received);
    close(pair[0]);
    close(pair[1]);

    int pipe_fds[2];
    TEST_CHECK(pipe(pipe_fds) == 0);
    TEST_CHECK(write_sync_byte(pipe_fds[1]) == 0);
    TEST_CHECK(read_sync_byte(pipe_fds[0]) == 0);
    close(pipe_fds[0]);
    TEST_CHECK(read_sync_byte(pipe_fds[1]) < 0);
    close(pipe_fds[1]);
    return 0;
}

static int test_identity_and_scheduler_helpers(void) {
    char *dir = test_mkdtemp_copy("bubblehub-sandbox-internal-env");
    TEST_CHECK(dir != NULL);

    setenv("BUBBLEHUB_AGENT_ID", "agt-internal", 1);
    uid_t agent_uid = sandbox_agent_uid(getuid());
    gid_t agent_gid = sandbox_agent_gid(getgid(), agent_uid);
    TEST_CHECK(agent_uid >= BUBBLEHUB_AGENT_UID_BASE);
    TEST_CHECK(agent_gid >= BUBBLEHUB_AGENT_UID_BASE);

    char state_path[512];
    snprintf(state_path, sizeof(state_path), "%s/scheduler.state", dir);
    setenv("BUBBLEHUB_SCHEDULER_STATE", state_path, 1);
    TEST_CHECK(setup_sandbox_scheduler_env(getuid(), "") == 0);
    TEST_CHECK(access(state_path, F_OK) == 0);

    unsetenv("BUBBLEHUB_SCHEDULER_STATE");
    setenv("XDG_RUNTIME_DIR", dir, 1);
    TEST_CHECK(setup_sandbox_scheduler_env(getuid(), "") == 0);
    TEST_CHECK(strstr(getenv("BUBBLEHUB_SCHEDULER_STATE"), "/bubblehub/scheduler.state") != NULL);

    char agent_dir[512];
    char root_dir[512];
    snprintf(agent_dir, sizeof(agent_dir), "%s/identity-agent", dir);
    snprintf(root_dir, sizeof(root_dir), "%s/root", dir);
    TEST_CHECK(test_mkdir_p_impl(agent_dir, 0700) == 0);
    TEST_CHECK(test_mkdir_p_impl(root_dir, 0700) == 0);
    TEST_CHECK(setup_sandbox_identity_files(agent_dir, root_dir, "agt_internal", "/home/agt_internal", agent_uid, agent_gid) == 0);

    free(dir);
    return 0;
}

static int test_setup_sandbox_home_and_runtime_env(void) {
    char *dir = test_mkdtemp_copy("bubblehub-sandbox-internal-home");
    TEST_CHECK(dir != NULL);

    char workspace[512];
    char identity[512];
    char target[512];
    snprintf(workspace, sizeof(workspace), "%s/workspace", dir);
    snprintf(identity, sizeof(identity), "%s/identity", dir);
    snprintf(target, sizeof(target), "%s/root", dir);
    TEST_CHECK(test_mkdir_p_impl(workspace, 0700) == 0);
    TEST_CHECK(test_mkdir_p_impl(identity, 0700) == 0);
    TEST_CHECK(test_mkdir_p_impl(target, 0700) == 0);

    setenv("BUBBLEHUB_AGENT_ID", "agt-home", 1);
    setenv("BUBBLEHUB_AGENT_NAME", "Review/Agent", 1);
    setenv("BUBBLEHUB_PYTHONPATH", "/opt/bubblehub", 1);
    setenv("PYTHONPATH", "/host/path", 1);
    setenv("BUBBLEHUB_LOG_FILE", "/host/log", 1);

    char cwd[512];
    TEST_CHECK(setup_sandbox_runtime_env(workspace, identity, target, workspace, workspace, getuid(), 60010, 60010, cwd, sizeof(cwd)) == 0);
    TEST_CHECK_CONTAINS(cwd, "/home/agt-home/workspace");
    TEST_CHECK_STR(getenv("HOME"), "/home/agt-home");
    TEST_CHECK_STR(getenv("PYTHONPATH"), "/opt/bubblehub");
    TEST_CHECK(getenv("BUBBLEHUB_LOG_FILE") == NULL);
    TEST_CHECK_STR(getenv("PYTHONNOUSERSITE"), "1");
    TEST_CHECK_CONTAINS(getenv("PS1"), "BubbleHub ReviewAgent");

    TEST_CHECK(setup_sandbox_runtime_env("", identity, target, workspace, workspace, getuid(), 60010, 60010, cwd, sizeof(cwd)) == -EINVAL);
    free(dir);
    return 0;
}

static int test_resolve_proxy_port_and_cleanup(void) {
    bubblehub_sandbox_config cfg = {0};
    TEST_CHECK_EQ(resolve_sandbox_http_proxy_port(&cfg), 0U);
    cfg.isolate_network = 1;
    TEST_CHECK_EQ(resolve_sandbox_http_proxy_port(&cfg), BUBBLEHUB_HTTP_PROXY_DEFAULT_PORT);
    cfg.sandbox_http_proxy_port = 19000;
    TEST_CHECK_EQ(resolve_sandbox_http_proxy_port(&cfg), 19000U);
    cfg.sandbox_http_proxy_port = UINT32_MAX;
    TEST_CHECK_EQ(resolve_sandbox_http_proxy_port(&cfg), 0U);

    char *dir = test_mkdtemp_copy("bubblehub-sandbox-internal-cleanup");
    TEST_CHECK(dir != NULL);
    char child[512];
    snprintf(child, sizeof(child), "%s/child", dir);
    TEST_CHECK(write_text_file(child, "x", 0644) == 0);
    cleanup_sandbox_root(dir);
    TEST_CHECK(access(dir, F_OK) != 0);
    free(dir);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_prompt_and_agent_sanitizers();
    rc |= test_file_and_path_helpers();
    rc |= test_proxy_env_helpers();
    rc |= test_fd_and_sync_helpers();
    rc |= test_identity_and_scheduler_helpers();
    rc |= test_setup_sandbox_home_and_runtime_env();
    rc |= test_resolve_proxy_port_and_cleanup();
    return rc;
}
