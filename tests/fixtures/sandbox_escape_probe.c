#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/bpf.h>
#include <linux/perf_event.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sys/reboot.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

extern char **environ;

static int failures = 0;

static void record_if_success(const char *name, int rc) {
    if (rc == 0) {
        fprintf(stderr, "- %s\n", name);
        failures++;
    }
}

static const char *required_env(const char *name) {
    const char *value = getenv(name);
    if (value == NULL || value[0] == '\0') {
        fprintf(stderr, "missing required env: %s\n", name);
        exit(2);
    }
    return value;
}

static void unset_prefixed_env(const char *prefix) {
    size_t prefix_len = strlen(prefix);
    for (;;) {
        int removed = 0;
        for (char **cursor = environ; *cursor != NULL; cursor++) {
            if (strncmp(*cursor, prefix, prefix_len) == 0) {
                const char *equals = strchr(*cursor, '=');
                if (equals == NULL) {
                    continue;
                }
                size_t name_len = (size_t)(equals - *cursor);
                char name[256];
                if (name_len >= sizeof(name)) {
                    continue;
                }
                memcpy(name, *cursor, name_len);
                name[name_len] = '\0';
                unsetenv(name);
                removed = 1;
                break;
            }
        }
        if (!removed) {
            return;
        }
    }
}

static int env_with_prefix_exists(const char *prefix) {
    size_t prefix_len = strlen(prefix);
    for (char **cursor = environ; *cursor != NULL; cursor++) {
        if (strncmp(*cursor, prefix, prefix_len) == 0) {
            return 1;
        }
    }
    return 0;
}

static void strip_escape_env(void) {
    unset_prefixed_env("AGEOS_");
    const char *names[] = {
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    };
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        unsetenv(names[i]);
    }
    if (env_with_prefix_exists("AGEOS_")) {
        fprintf(stderr, "- AGEOS env vars remained available during C escape attempts\n");
        failures++;
    }
}

static int join_path(char *buffer, size_t buffer_size, const char *left, const char *right) {
    int written = snprintf(buffer, buffer_size, "%s/%s", left, right);
    return written >= 0 && (size_t)written < buffer_size ? 0 : 1;
}

static int sibling_path(char *buffer, size_t buffer_size, const char *path, const char *name) {
    const char *slash = strrchr(path, '/');
    if (slash == NULL) {
        return 1;
    }
    size_t parent_len = (size_t)(slash - path);
    if (parent_len + 1 + strlen(name) + 1 > buffer_size) {
        return 1;
    }
    memcpy(buffer, path, parent_len);
    buffer[parent_len] = '/';
    strcpy(buffer + parent_len + 1, name);
    return 0;
}

static int read_file(const char *path) {
    int fd = (int)syscall(SYS_openat, AT_FDCWD, path, O_RDONLY | O_CLOEXEC, 0);
    if (fd < 0) {
        return 1;
    }
    char buffer[16];
    ssize_t received = (ssize_t)syscall(SYS_read, fd, buffer, sizeof(buffer));
    syscall(SYS_close, fd);
    return received >= 0 ? 0 : 1;
}

static int write_file(const char *path) {
    int fd = (int)syscall(SYS_openat, AT_FDCWD, path, O_WRONLY | O_TRUNC, 0);
    if (fd < 0) {
        return 1;
    }
    ssize_t written = (ssize_t)syscall(SYS_write, fd, "escaped\n", 8);
    syscall(SYS_close, fd);
    return written == 8 ? 0 : 1;
}

static int create_file(const char *path) {
    int fd = (int)syscall(SYS_openat, AT_FDCWD, path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) {
        return 1;
    }
    syscall(SYS_close, fd);
    return 0;
}

static int unlink_path(const char *path) {
    return syscall(SYS_unlinkat, AT_FDCWD, path, 0) == 0 ? 0 : 1;
}

static int hardlink_path(const char *source, const char *target) {
    return syscall(SYS_linkat, AT_FDCWD, source, AT_FDCWD, target, 0) == 0 ? 0 : 1;
}

static int rename_path(const char *source, const char *target) {
    return syscall(SYS_renameat, AT_FDCWD, source, AT_FDCWD, target) == 0 ? 0 : 1;
}

static int mount_outside_workspace(void) {
    return syscall(SYS_mount, "tmpfs", "/mnt", "tmpfs", 0, "size=4k") == 0 ? 0 : 1;
}

static int pivot_root_outside_workspace(void) {
#ifdef SYS_pivot_root
    return syscall(SYS_pivot_root, "/", "/") == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int setns_host_mount_namespace(void) {
    int fd = (int)syscall(SYS_openat, AT_FDCWD, "/proc/1/ns/mnt", O_RDONLY | O_CLOEXEC, 0);
    if (fd < 0) {
        return 1;
    }
    int rc = syscall(SYS_setns, fd, 0);
    syscall(SYS_close, fd);
    return rc == 0 ? 0 : 1;
}

static int ptrace_host_init(void) {
    return syscall(SYS_ptrace, PTRACE_ATTACH, 1, 0, 0) == 0 ? 0 : 1;
}

static int unix_socket_connect(const char *path) {
    int fd = (int)syscall(SYS_socket, AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return 1;
    }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
    int rc = syscall(SYS_connect, fd, &addr, sizeof(addr));
    syscall(SYS_close, fd);
    return rc == 0 ? 0 : 1;
}

static int connect_public_network(void) {
    signal(SIGALRM, SIG_DFL);
    alarm(2);
    int fd = (int)syscall(SYS_socket, AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return 1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(80);
    if (inet_pton(AF_INET, "1.1.1.1", &addr.sin_addr) != 1) {
        syscall(SYS_close, fd);
        return 1;
    }
    int rc = syscall(SYS_connect, fd, &addr, sizeof(addr));
    syscall(SYS_close, fd);
    return rc == 0 ? 0 : 1;
}

static int bpf_map_create(void) {
#ifdef SYS_bpf
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.map_type = BPF_MAP_TYPE_ARRAY;
    attr.key_size = 4;
    attr.value_size = 4;
    attr.max_entries = 1;
    int fd = (int)syscall(SYS_bpf, BPF_MAP_CREATE, &attr, sizeof(attr));
    if (fd < 0) {
        return 1;
    }
    syscall(SYS_close, fd);
    return 0;
#else
    return 1;
#endif
}

static int perf_event_open_probe(void) {
#ifdef SYS_perf_event_open
    struct perf_event_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.type = PERF_TYPE_SOFTWARE;
    attr.size = sizeof(attr);
    attr.config = PERF_COUNT_SW_CPU_CLOCK;
    attr.disabled = 1;
    int fd = (int)syscall(SYS_perf_event_open, &attr, 1, -1, -1, 0);
    if (fd < 0) {
        return 1;
    }
    syscall(SYS_close, fd);
    return 0;
#else
    return 1;
#endif
}

static int init_module_probe(void) {
#ifdef SYS_init_module
    return syscall(SYS_init_module, "", 0, "") == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int finit_module_probe(void) {
#ifdef SYS_finit_module
    int fd = (int)syscall(SYS_openat, AT_FDCWD, "/dev/null", O_RDONLY | O_CLOEXEC, 0);
    if (fd < 0) {
        return 1;
    }
    int rc = syscall(SYS_finit_module, fd, "", 0);
    syscall(SYS_close, fd);
    return rc == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int delete_module_probe(void) {
#ifdef SYS_delete_module
    return syscall(SYS_delete_module, "ageos_escape_probe", 0) == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int kexec_load_probe(void) {
#ifdef SYS_kexec_load
    return syscall(SYS_kexec_load, 0, 0, NULL, 0) == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int reboot_probe(void) {
#ifdef SYS_reboot
    return syscall(SYS_reboot, 0xfee1dead, 672274793, 0x1234567, NULL) == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int swapon_probe(const char *path) {
#ifdef SYS_swapon
    return syscall(SYS_swapon, path, 0) == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int iopl_probe(void) {
#ifdef SYS_iopl
    return syscall(SYS_iopl, 3) == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static int ioperm_probe(void) {
#ifdef SYS_ioperm
    return syscall(SYS_ioperm, 0, 1, 1) == 0 ? 0 : 1;
#else
    return 1;
#endif
}

static void sanity_check_workspace(const char *workspace) {
    char path[4096];
    if (join_path(path, sizeof(path), workspace, "inside-c-workspace.txt") != 0 || create_file(path) != 0) {
        fprintf(stderr, "- create normal in-sandbox C workspace file\n");
        failures++;
    }
}

static void run_fs_direct(const char *host_canary) {
    char path[4096];
    record_if_success("syscall read host canary outside workspace", read_file(host_canary));
    record_if_success("syscall overwrite host canary outside workspace", write_file(host_canary));
    record_if_success("syscall unlink host canary outside workspace", unlink_path(host_canary));
    if (sibling_path(path, sizeof(path), host_canary, "created-by-c-sandbox") == 0) {
        record_if_success("syscall create sibling outside workspace", create_file(path));
    }
}

static void run_proc_root(const char *host_canary) {
    char path[4096];
    snprintf(path, sizeof(path), "/proc/self/root%s", host_canary);
    record_if_success("syscall read host canary through proc self root", read_file(path));
    record_if_success("syscall write host canary through proc self root", write_file(path));
    snprintf(path, sizeof(path), "/proc/1/root%s", host_canary);
    record_if_success("syscall read host canary through proc init root", read_file(path));
    record_if_success("syscall write host canary through proc init root", write_file(path));
    snprintf(path, sizeof(path), "/proc/thread-self/root%s", host_canary);
    record_if_success("syscall read host canary through proc thread root", read_file(path));
    record_if_success("syscall write host canary through proc thread root", write_file(path));
}

static void run_fs_links(const char *workspace, const char *host_canary) {
    char path[4096];
    char path2[4096];
    if (join_path(path, sizeof(path), workspace, "canary-hardlink-c") == 0) {
        record_if_success("syscall hardlink host canary into workspace", hardlink_path(host_canary, path));
    }
    if (join_path(path, sizeof(path), workspace, "rename-source-c") != 0) {
        fprintf(stderr, "- build C rename source path\n");
        failures++;
        return;
    }
    if (create_file(path) != 0) {
        fprintf(stderr, "- create C rename source\n");
        failures++;
        return;
    }
    record_if_success("syscall rename workspace file over host canary", rename_path(path, host_canary));
}

static void run_protected_writes(void) {
    record_if_success("syscall write /etc/passwd", write_file("/etc/passwd"));
    record_if_success("syscall write /usr/local/bin/ageos", write_file("/usr/local/bin/ageos"));
    record_if_success("syscall write /opt/ageos", create_file("/opt/ageos/.ageos-escape-c"));
    record_if_success("syscall write host /tmp", create_file("/tmp/ageos-sandbox-escape-c"));
    record_if_success("syscall write proc sysctl", write_file("/proc/sys/kernel/hostname"));
}

static void run_protected_reads(void) {
    record_if_success("syscall read /etc/shadow", read_file("/etc/shadow"));
    record_if_success("syscall read /root ssh key", read_file("/root/.ssh/id_rsa"));
}

static void run_protected_dirs(void) {
    record_if_success("syscall write /root", create_file("/root/ageos-sandbox-escape"));
    record_if_success("syscall write /var/tmp", create_file("/var/tmp/ageos-sandbox-escape"));
    record_if_success("syscall write /run", create_file("/run/ageos-sandbox-escape"));
    record_if_success("syscall write /dev", create_file("/dev/ageos-sandbox-escape"));
}

static void run_runtime_sockets(void) {
    record_if_success("syscall connect docker socket", unix_socket_connect("/var/run/docker.sock"));
    record_if_success("syscall connect run docker socket", unix_socket_connect("/run/docker.sock"));
    record_if_success("syscall connect containerd socket", unix_socket_connect("/run/containerd/containerd.sock"));
    record_if_success("syscall connect podman socket", unix_socket_connect("/run/podman/podman.sock"));
}

static void run_namespace(void) {
    record_if_success("syscall mount outside workspace", mount_outside_workspace());
    record_if_success("syscall pivot_root outside workspace", pivot_root_outside_workspace());
    record_if_success("syscall setns host mount namespace", setns_host_mount_namespace());
    record_if_success("syscall ptrace host pid 1", ptrace_host_init());
}

static void run_kernel(void) {
    record_if_success("syscall bpf map create", bpf_map_create());
    record_if_success("syscall perf event open host pid 1", perf_event_open_probe());
    record_if_success("syscall init_module", init_module_probe());
    record_if_success("syscall finit_module", finit_module_probe());
    record_if_success("syscall delete_module", delete_module_probe());
    record_if_success("syscall kexec_load", kexec_load_probe());
    record_if_success("syscall reboot", reboot_probe());
    record_if_success("syscall iopl", iopl_probe());
    record_if_success("syscall ioperm", ioperm_probe());
}

static void run_swap(const char *workspace) {
    char path[4096];
    if (join_path(path, sizeof(path), workspace, "inside-c-workspace.txt") == 0) {
        record_if_success("syscall swapon workspace file", swapon_probe(path));
    }
}

static void run_network_isolated(int expect_network_blocked) {
    if (expect_network_blocked) {
        record_if_success("syscall public network connect while network is isolated", connect_public_network());
    }
}

static int connect_host_endpoint(const char *ip, uint16_t port) {
    signal(SIGALRM, SIG_DFL);
    alarm(2);
    int fd = (int)syscall(SYS_socket, AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return 1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) {
        syscall(SYS_close, fd);
        return 1;
    }
    int rc = syscall(SYS_connect, fd, &addr, sizeof(addr));
    syscall(SYS_close, fd);
    return rc == 0 ? 0 : 1;
}

static void run_endpoint_repoint(int expect_network_blocked) {
    static const char *candidate_ips[] = {
        "10.0.0.1",
        "192.168.0.1",
        "192.168.1.1",
        "172.17.0.1",
        "172.18.0.1",
        "1.1.1.1",
        "203.0.113.1",
    };
    char label[128];

    if (!expect_network_blocked) {
        return;
    }
    for (size_t i = 0; i < sizeof(candidate_ips) / sizeof(candidate_ips[0]); i++) {
        const char *ip = candidate_ips[i];
        snprintf(label, sizeof(label), "syscall connect to repointed inference host %s", ip);
        record_if_success(label, connect_host_endpoint(ip, 8000));
        snprintf(label, sizeof(label), "syscall connect to repointed proxy host %s", ip);
        record_if_success(label, connect_host_endpoint(ip, 8080));
    }
}

static int run_category(const char *category, const char *workspace, const char *host_canary, int expect_network_blocked) {
    if (strcmp(category, "env") == 0) {
        return 0;
    }
    if (strcmp(category, "fs-direct") == 0) {
        run_fs_direct(host_canary);
    } else if (strcmp(category, "proc-root") == 0) {
        run_proc_root(host_canary);
    } else if (strcmp(category, "fs-links") == 0) {
        run_fs_links(workspace, host_canary);
    } else if (strcmp(category, "protected-writes") == 0) {
        run_protected_writes();
    } else if (strcmp(category, "protected-reads") == 0) {
        run_protected_reads();
    } else if (strcmp(category, "protected-dirs") == 0) {
        run_protected_dirs();
    } else if (strcmp(category, "runtime-sockets") == 0) {
        run_runtime_sockets();
    } else if (strcmp(category, "namespace") == 0) {
        run_namespace();
    } else if (strcmp(category, "kernel") == 0) {
        run_kernel();
    } else if (strcmp(category, "swap") == 0) {
        run_swap(workspace);
    } else if (strcmp(category, "network-isolated") == 0) {
        run_network_isolated(expect_network_blocked);
    } else if (strcmp(category, "endpoint-repoint") == 0) {
        run_endpoint_repoint(expect_network_blocked);
    } else if (strcmp(category, "all") == 0) {
        run_fs_direct(host_canary);
        run_proc_root(host_canary);
        run_fs_links(workspace, host_canary);
        run_protected_writes();
        run_protected_reads();
        run_protected_dirs();
        run_runtime_sockets();
        run_namespace();
        run_kernel();
        run_swap(workspace);
        run_network_isolated(expect_network_blocked);
        run_endpoint_repoint(expect_network_blocked);
    } else {
        fprintf(stderr, "unknown C escape category: %s\n", category);
        return 2;
    }
    return failures == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    const char *workspace = required_env("AGEOS_WORKSPACE");
    const char *host_canary = required_env("HOST_CANARY");
    int expect_network_blocked = strcmp(required_env("EXPECT_NETWORK_BLOCKED"), "1") == 0;
    const char *category = argc > 1 ? argv[1] : "all";

    sanity_check_workspace(workspace);
    strip_escape_env();
    int rc = run_category(category, workspace, host_canary, expect_network_blocked);

    if (rc == 0 && failures == 0) {
        return 0;
    }
    if (rc != 2) {
        fprintf(stderr, "C sandbox escape attempts unexpectedly succeeded (%s):\n", category);
    }
    return rc == 2 ? 2 : 1;
}
