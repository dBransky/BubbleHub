#define _GNU_SOURCE

#include "ageos/limits.h"
#include "ageos/log.h"
#include "ageos/sandbox.h"

#include <errno.h>
#include <ftw.h>
#include <limits.h>
#include <stdio.h>

#ifdef __linux__
#include <arpa/inet.h>
#include <fcntl.h>
#include <net/if.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sched.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>

#if __has_include(<seccomp.h>)
#include <seccomp.h>
#define AGEOS_HAS_SECCOMP 1
#else
#define AGEOS_HAS_SECCOMP 0
#endif

#define AGEOS_AGENT_UID_BASE 60000U
#define AGEOS_AGENT_UID_SPAN 4000U

extern int ageos_landlock_apply_filesystem(const char *writable_dir);

static int apply_no_new_privs(void) {
    return prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
}

static void close_extra_fds(void) {
    long max_fd = sysconf(_SC_OPEN_MAX);
    if (max_fd < 0) {
        max_fd = 1024;
    }
    for (int fd = 3; fd < max_fd; fd++) {
        close(fd);
    }
}

static int apply_seccomp(void) {
#if AGEOS_HAS_SECCOMP
    scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_KILL_PROCESS);
    if (ctx == NULL) {
        return -ENOMEM;
    }
    int syscalls[] = {
        SCMP_SYS(read), SCMP_SYS(write), SCMP_SYS(close), SCMP_SYS(exit),
        SCMP_SYS(exit_group), SCMP_SYS(futex), SCMP_SYS(brk), SCMP_SYS(mmap),
        SCMP_SYS(mprotect), SCMP_SYS(munmap), SCMP_SYS(rt_sigaction),
        SCMP_SYS(rt_sigprocmask), SCMP_SYS(clock_gettime), SCMP_SYS(nanosleep),
        SCMP_SYS(getpid), SCMP_SYS(gettid), SCMP_SYS(getrandom), SCMP_SYS(openat),
        SCMP_SYS(newfstatat), SCMP_SYS(fstat), SCMP_SYS(lseek), SCMP_SYS(readlinkat),
        SCMP_SYS(access), SCMP_SYS(execve), SCMP_SYS(arch_prctl), SCMP_SYS(set_tid_address),
        SCMP_SYS(set_robust_list), SCMP_SYS(prlimit64), SCMP_SYS(getcwd),
    };
    size_t count = sizeof(syscalls) / sizeof(syscalls[0]);
    for (size_t i = 0; i < count; i++) {
        seccomp_rule_add(ctx, SCMP_ACT_ALLOW, syscalls[i], 0);
    }
    int rc = seccomp_load(ctx);
    seccomp_release(ctx);
    return rc;
#else
    return 0;
#endif
}

static int setup_mounts(const char *new_root, const char *workdir, const char *root_dir) {
    (void)new_root;
    (void)workdir;
    (void)root_dir;
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) != 0) {
        return -errno;
    }
    return 0;
}

static int setup_loopback(void) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        return -errno;
    }
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, "lo", IFNAMSIZ - 1);
    if (ioctl(fd, SIOCGIFFLAGS, &ifr) != 0) {
        int err = errno;
        close(fd);
        return -err;
    }
    ifr.ifr_flags |= IFF_UP | IFF_RUNNING;
    if (ioctl(fd, SIOCSIFFLAGS, &ifr) != 0) {
        int err = errno;
        close(fd);
        return -err;
    }
    close(fd);
    return 0;
}

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

static int send_fd(int socket_fd, int fd_to_send) {
    char control[CMSG_SPACE(sizeof(int))];
    char byte = 0;
    struct iovec iov = {
        .iov_base = &byte,
        .iov_len = sizeof(byte),
    };
    struct msghdr msg;
    memset(&msg, 0, sizeof(msg));
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control;
    msg.msg_controllen = sizeof(control);

    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(cmsg), &fd_to_send, sizeof(int));
    msg.msg_controllen = cmsg->cmsg_len;

    while (sendmsg(socket_fd, &msg, 0) < 0) {
        if (errno == EINTR) {
            continue;
        }
        return -errno;
    }
    return 0;
}

static int recv_fd(int socket_fd) {
    char control[CMSG_SPACE(sizeof(int))];
    char byte = 0;
    struct iovec iov = {
        .iov_base = &byte,
        .iov_len = sizeof(byte),
    };
    struct msghdr msg;
    memset(&msg, 0, sizeof(msg));
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control;
    msg.msg_controllen = sizeof(control);

    ssize_t received;
    do {
        received = recvmsg(socket_fd, &msg, 0);
    } while (received < 0 && errno == EINTR);
    if (received <= 0) {
        return -1;
    }
    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    if (cmsg == NULL || cmsg->cmsg_level != SOL_SOCKET || cmsg->cmsg_type != SCM_RIGHTS) {
        return -1;
    }
    int received_fd = -1;
    memcpy(&received_fd, CMSG_DATA(cmsg), sizeof(int));
    return received_fd;
}

static void reap_proxy_children(void) {
    int status = 0;
    while (waitpid(-1, &status, WNOHANG) > 0) {
    }
}

static int connect_to_inference(const char *host, uint32_t port) {
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

static int create_loopback_listener(uint32_t port) {
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

static void host_inference_proxy(int control_fd, const char *host, uint32_t port) {
    for (;;) {
        int data_fd = recv_fd(control_fd);
        if (data_fd < 0) {
            break;
        }
        pid_t pid = fork();
        if (pid == 0) {
            close(control_fd);
            int upstream_fd = connect_to_inference(host, port);
            if (upstream_fd >= 0) {
                proxy_loop(data_fd, upstream_fd);
                close(upstream_fd);
            }
            close(data_fd);
            _exit(0);
        }
        close(data_fd);
        reap_proxy_children();
    }
    reap_proxy_children();
}

static void namespace_inference_proxy(int control_fd, uint32_t listen_port) {
    prctl(PR_SET_PDEATHSIG, SIGTERM, 0, 0, 0);
    if (getppid() == 1) {
        _exit(0);
    }
    int listener_fd = create_loopback_listener(listen_port);
    if (listener_fd < 0) {
        AGEOS_LOG_ERROR("failed to expose inference endpoint", "%s", strerror(-listener_fd));
        _exit(126);
    }
    for (;;) {
        int client_fd = accept(listener_fd, NULL, NULL);
        if (client_fd < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        int pair[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair) != 0) {
            close(client_fd);
            continue;
        }
        if (send_fd(control_fd, pair[1]) != 0) {
            close(pair[0]);
            close(pair[1]);
            close(client_fd);
            break;
        }
        close(pair[1]);
        pid_t pid = fork();
        if (pid == 0) {
            close(listener_fd);
            close(control_fd);
            proxy_loop(client_fd, pair[0]);
            close(pair[0]);
            close(client_fd);
            _exit(0);
        }
        close(pair[0]);
        close(client_fd);
        reap_proxy_children();
    }
    close(listener_fd);
    reap_proxy_children();
}

static int start_namespace_inference_proxy(int control_fd, uint32_t listen_port) {
    pid_t pid = fork();
    if (pid < 0) {
        return -errno;
    }
    if (pid == 0) {
        namespace_inference_proxy(control_fd, listen_port);
        _exit(0);
    }
    return 0;
}

static int write_file(const char *path, const char *value) {
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        return -errno;
    }
    size_t len = strlen(value);
    ssize_t written = write(fd, value, len);
    int err = errno;
    close(fd);
    if (written != (ssize_t)len) {
        return -err;
    }
    return 0;
}

static int remove_tree_entry(const char *path, const struct stat *st, int type, struct FTW *ftwbuf) {
    (void)st;
    (void)type;
    (void)ftwbuf;
    return remove(path);
}

static void cleanup_sandbox_root(const char *path) {
    if (path != NULL && path[0] != '\0') {
        nftw(path, remove_tree_entry, 16, FTW_DEPTH | FTW_PHYS);
    }
}

static int touch_file(const char *path) {
    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) {
        return -errno;
    }
    close(fd);
    return 0;
}

static uint32_t hash_agent_id(const char *agent_id) {
    uint32_t hash = 2166136261U;
    if (agent_id == NULL || agent_id[0] == '\0') {
        agent_id = "agent";
    }
    for (const unsigned char *cursor = (const unsigned char *)agent_id; *cursor != '\0'; cursor++) {
        hash ^= (uint32_t)(*cursor);
        hash *= 16777619U;
    }
    return hash;
}

static uid_t sandbox_agent_uid(uid_t host_uid) {
    const char *agent_id = getenv("AGEOS_AGENT_ID");
    uid_t uid = (uid_t)(AGEOS_AGENT_UID_BASE + (hash_agent_id(agent_id) % AGEOS_AGENT_UID_SPAN));
    if (uid == host_uid) {
        uid = (uid_t)(AGEOS_AGENT_UID_BASE + ((uid + 1U - AGEOS_AGENT_UID_BASE) % AGEOS_AGENT_UID_SPAN));
    }
    return uid;
}

static gid_t sandbox_agent_gid(gid_t host_gid, uid_t agent_uid) {
    gid_t gid = (gid_t)agent_uid;
    if (gid == host_gid) {
        gid = (gid_t)(AGEOS_AGENT_UID_BASE + ((gid + 1U - AGEOS_AGENT_UID_BASE) % AGEOS_AGENT_UID_SPAN));
    }
    return gid;
}

static void sanitize_agent_name(const char *agent_id, char *buffer, size_t buffer_size) {
    if (buffer_size == 0) {
        return;
    }
    if (agent_id == NULL || agent_id[0] == '\0') {
        agent_id = "agent";
    }
    size_t offset = 0;
    for (const unsigned char *cursor = (const unsigned char *)agent_id; *cursor != '\0' && offset + 1 < buffer_size; cursor++) {
        unsigned char ch = *cursor;
        if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_') {
            buffer[offset++] = (char)ch;
        } else {
            buffer[offset++] = '_';
        }
    }
    buffer[offset] = '\0';
    if (offset == 0) {
        snprintf(buffer, buffer_size, "agent");
    }
}

static int mkdir_if_missing(const char *path, mode_t mode) {
    if (mkdir(path, mode) == 0 || errno == EEXIST) {
        return 0;
    }
    return -errno;
}

static int write_text_file(const char *path, const char *content, mode_t mode) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (fd < 0) {
        return -errno;
    }
    int rc = write_all(fd, content, strlen(content));
    int err = errno;
    close(fd);
    return rc == 0 ? 0 : -err;
}

static int bind_file_readonly(const char *source, const char *target) {
    if (mount(source, target, NULL, MS_BIND, NULL) != 0) {
        return -errno;
    }
    if (mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY, NULL) != 0) {
        return -errno;
    }
    return 0;
}

static int bind_dir(const char *source, const char *target) {
    if (mount(source, target, NULL, MS_BIND, NULL) != 0) {
        return -errno;
    }
    return 0;
}

static int relative_workdir(const char *workdir, const char *root_dir, char *buffer, size_t buffer_size) {
    if (buffer_size == 0) {
        return -EINVAL;
    }
    buffer[0] = '\0';
    if (root_dir == NULL || root_dir[0] == '\0') {
        return 0;
    }
    if (workdir == NULL || workdir[0] == '\0' || strcmp(workdir, root_dir) == 0) {
        return 0;
    }
    const char *workspace_mount = "/workspace";
    size_t workspace_len = strlen(workspace_mount);
    if (strcmp(workdir, workspace_mount) == 0) {
        return 0;
    }
    if (strncmp(workdir, workspace_mount, workspace_len) == 0 && workdir[workspace_len] == '/') {
        int written = snprintf(buffer, buffer_size, "%s", workdir + workspace_len + 1);
        return (written < 0 || (size_t)written >= buffer_size) ? -ENAMETOOLONG : 0;
    }
    size_t root_len = strlen(root_dir);
    if (strncmp(workdir, root_dir, root_len) != 0 || workdir[root_len] != '/') {
        return -EINVAL;
    }
    int written = snprintf(buffer, buffer_size, "%s", workdir + root_len + 1);
    return (written < 0 || (size_t)written >= buffer_size) ? -ENAMETOOLONG : 0;
}

static int setup_sandbox_scheduler_env(uid_t host_uid) {
    const char *explicit_path = getenv("AGEOS_SCHEDULER_STATE");
    if (explicit_path != NULL && explicit_path[0] != '\0') {
        return touch_file(explicit_path);
    }

    const char *runtime_dir = getenv("XDG_RUNTIME_DIR");
    char state_dir[PATH_MAX];
    if (runtime_dir != NULL && runtime_dir[0] != '\0') {
        int written = snprintf(state_dir, sizeof(state_dir), "%s/ageos", runtime_dir);
        if (written < 0 || (size_t)written >= sizeof(state_dir)) {
            return -ENAMETOOLONG;
        }
    } else {
        int written = snprintf(state_dir, sizeof(state_dir), "/tmp/ageos-%lu", (unsigned long)host_uid);
        if (written < 0 || (size_t)written >= sizeof(state_dir)) {
            return -ENAMETOOLONG;
        }
    }
    if (mkdir(state_dir, 0700) != 0 && errno != EEXIST) {
        return -errno;
    }
    char state_path[PATH_MAX];
    int written = snprintf(state_path, sizeof(state_path), "%s/scheduler.state", state_dir);
    if (written < 0 || (size_t)written >= sizeof(state_path)) {
        return -ENAMETOOLONG;
    }
    int touch_rc = touch_file(state_path);
    if (touch_rc != 0) {
        return touch_rc;
    }
    setenv("AGEOS_SCHEDULER_STATE", state_path, 1);
    return 0;
}

static int setup_sandbox_identity_files(
    const char *agent_dir,
    const char *agent_name,
    const char *home_path,
    uid_t agent_uid,
    gid_t agent_gid
) {
    char passwd_path[PATH_MAX];
    char group_path[PATH_MAX];
    int written = snprintf(passwd_path, sizeof(passwd_path), "%s/passwd", agent_dir);
    if (written < 0 || (size_t)written >= sizeof(passwd_path)) {
        return -ENAMETOOLONG;
    }
    written = snprintf(group_path, sizeof(group_path), "%s/group", agent_dir);
    if (written < 0 || (size_t)written >= sizeof(group_path)) {
        return -ENAMETOOLONG;
    }

    char passwd_content[PATH_MAX + 256];
    char group_content[256];
    written = snprintf(
        passwd_content,
        sizeof(passwd_content),
        "root:x:0:0:root:/root:/usr/sbin/nologin\n%s:x:%u:%u:AgeOS Agent:%s:/bin/sh\n",
        agent_name,
        (unsigned int)agent_uid,
        (unsigned int)agent_gid,
        home_path
    );
    if (written < 0 || (size_t)written >= sizeof(passwd_content)) {
        return -ENAMETOOLONG;
    }
    written = snprintf(
        group_content,
        sizeof(group_content),
        "root:x:0:\n%s:x:%u:\n",
        agent_name,
        (unsigned int)agent_gid
    );
    if (written < 0 || (size_t)written >= sizeof(group_content)) {
        return -ENAMETOOLONG;
    }

    int rc = write_text_file(passwd_path, passwd_content, 0644);
    if (rc != 0) {
        return rc;
    }
    rc = write_text_file(group_path, group_content, 0644);
    if (rc != 0) {
        return rc;
    }
    rc = bind_file_readonly(passwd_path, "/etc/passwd");
    if (rc != 0) {
        return rc;
    }
    return bind_file_readonly(group_path, "/etc/group");
}

static int setup_sandbox_home(
    const char *writable_dir,
    const char *identity_root,
    const char *workdir,
    const char *root_dir,
    uid_t agent_uid,
    gid_t agent_gid,
    char *workspace_cwd,
    size_t workspace_cwd_size
) {
    char agent_name[128];
    sanitize_agent_name(getenv("AGEOS_AGENT_ID"), agent_name, sizeof(agent_name));

    char ageos_dir[PATH_MAX];
    char agents_dir[PATH_MAX];
    char agent_dir[PATH_MAX];
    char identity_dir[PATH_MAX];
    char identity_agent_dir[PATH_MAX];
    int written = snprintf(ageos_dir, sizeof(ageos_dir), "%s/.ageos", writable_dir);
    if (written < 0 || (size_t)written >= sizeof(ageos_dir)) {
        return -ENAMETOOLONG;
    }
    written = snprintf(agents_dir, sizeof(agents_dir), "%s/agents", ageos_dir);
    if (written < 0 || (size_t)written >= sizeof(agents_dir)) {
        return -ENAMETOOLONG;
    }
    written = snprintf(agent_dir, sizeof(agent_dir), "%s/%s", agents_dir, agent_name);
    if (written < 0 || (size_t)written >= sizeof(agent_dir)) {
        return -ENAMETOOLONG;
    }
    written = snprintf(identity_dir, sizeof(identity_dir), "%s/identity", identity_root);
    if (written < 0 || (size_t)written >= sizeof(identity_dir)) {
        return -ENAMETOOLONG;
    }
    written = snprintf(identity_agent_dir, sizeof(identity_agent_dir), "%s/%s", identity_dir, agent_name);
    if (written < 0 || (size_t)written >= sizeof(identity_agent_dir)) {
        return -ENAMETOOLONG;
    }
    char backing_home_path[PATH_MAX];
    written = snprintf(backing_home_path, sizeof(backing_home_path), "%s/home", agent_dir);
    if (written < 0 || (size_t)written >= sizeof(backing_home_path)) {
        return -ENAMETOOLONG;
    }
    int rc = mkdir_if_missing(ageos_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = mkdir_if_missing(agents_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = mkdir_if_missing(agent_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = mkdir_if_missing(identity_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = mkdir_if_missing(identity_agent_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = mkdir_if_missing(backing_home_path, 0700);
    if (rc != 0) {
        return rc;
    }

    if (mount("tmpfs", "/home", "tmpfs", MS_NOSUID | MS_NODEV, "size=64m") != 0) {
        return -errno;
    }

    char visible_home_path[PATH_MAX];
    written = snprintf(visible_home_path, sizeof(visible_home_path), "/home/%s", agent_name);
    if (written < 0 || (size_t)written >= sizeof(visible_home_path)) {
        return -ENAMETOOLONG;
    }
    rc = mkdir_if_missing(visible_home_path, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = bind_dir(backing_home_path, visible_home_path);
    if (rc != 0) {
        return rc;
    }

    char visible_workspace_path[PATH_MAX];
    written = snprintf(visible_workspace_path, sizeof(visible_workspace_path), "%s/workspace", visible_home_path);
    if (written < 0 || (size_t)written >= sizeof(visible_workspace_path)) {
        return -ENAMETOOLONG;
    }
    rc = mkdir_if_missing(visible_workspace_path, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = bind_dir(writable_dir, visible_workspace_path);
    if (rc != 0) {
        return rc;
    }

    char relative[PATH_MAX];
    rc = relative_workdir(workdir, root_dir, relative, sizeof(relative));
    if (rc != 0) {
        return rc;
    }
    if (relative[0] == '\0') {
        written = snprintf(workspace_cwd, workspace_cwd_size, "%s", visible_workspace_path);
    } else {
        written = snprintf(workspace_cwd, workspace_cwd_size, "%s/%s", visible_workspace_path, relative);
    }
    if (written < 0 || (size_t)written >= workspace_cwd_size) {
        return -ENAMETOOLONG;
    }
    if (chdir(workspace_cwd) != 0) {
        return -errno;
    }
    setenv("PWD", workspace_cwd, 1);

    char tmp_path[PATH_MAX];
    written = snprintf(tmp_path, sizeof(tmp_path), "%s/tmp", visible_home_path);
    if (written < 0 || (size_t)written >= sizeof(tmp_path)) {
        return -ENAMETOOLONG;
    }
    rc = mkdir_if_missing(tmp_path, 0700);
    if (rc != 0) {
        return rc;
    }

    setenv("HOME", visible_home_path, 1);
    setenv("TMPDIR", tmp_path, 1);
    setenv("USER", agent_name, 1);
    setenv("LOGNAME", agent_name, 1);
    setenv("SHELL", "/bin/sh", 1);
    setenv("PATH", "/usr/local/bin:/opt/ageos/bin:/usr/bin:/bin", 1);
    setenv("LANG", "en_US.UTF-8", 1);
    setenv("LANGUAGE", "en_US.UTF-8", 1);
    setenv("TERM", "xterm-256color", 1);
    setenv("PS1", "\\[\\e[1;32m\\]\\u\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]\\$ ", 1);
    setenv("AGEOS_AGENT_HOME", visible_home_path, 1);
    setenv("AGEOS_WORKSPACE", visible_workspace_path, 1);
    return setup_sandbox_identity_files(identity_agent_dir, agent_name, visible_home_path, agent_uid, agent_gid);
}

static int setup_sandbox_runtime_env(
    const char *writable_dir,
    const char *identity_root,
    const char *workdir,
    const char *root_dir,
    uid_t host_uid,
    uid_t agent_uid,
    gid_t agent_gid
) {
    if (writable_dir == NULL || writable_dir[0] == '\0') {
        return -EINVAL;
    }
    char workspace_cwd[PATH_MAX];
    int home_rc = setup_sandbox_home(
        writable_dir,
        identity_root,
        workdir,
        root_dir,
        agent_uid,
        agent_gid,
        workspace_cwd,
        sizeof(workspace_cwd)
    );
    if (home_rc != 0) {
        return home_rc;
    }
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONUSERBASE");
    unsetenv("AGEOS_LOG_FILE");
    setenv("PYTHONNOUSERSITE", "1", 1);
    return setup_sandbox_scheduler_env(host_uid);
}

static int setup_user_namespace(uid_t agent_uid, gid_t agent_gid) {
    uid_t uid = getuid();
    gid_t gid = getgid();
    if (unshare(CLONE_NEWUSER) != 0) {
        return -errno;
    }

    char map[128];
    snprintf(map, sizeof(map), "%u %u 1\n", (unsigned int)agent_uid, (unsigned int)uid);
    int rc = write_file("/proc/self/uid_map", map);
    if (rc != 0) {
        return rc;
    }

    write_file("/proc/self/setgroups", "deny\n");
    snprintf(map, sizeof(map), "%u %u 1\n", (unsigned int)agent_gid, (unsigned int)gid);
    rc = write_file("/proc/self/gid_map", map);
    if (rc != 0) {
        return rc;
    }

    if (setgid(agent_gid) != 0 || setuid(agent_uid) != 0) {
        return -errno;
    }
    return 0;
}

int ageos_sandbox_run(const ageos_sandbox_config *cfg) {
    ageos_log_init();
    if (cfg == NULL || cfg->binary == NULL || cfg->argv == NULL || cfg->workdir == NULL) {
        AGEOS_LOG_ERROR("invalid sandbox configuration", "");
        return -EINVAL;
    }
    AGEOS_LOG_INFO(
        "starting sandbox",
        "binary=%s workdir=%s isolate_network=%d",
        cfg->binary,
        cfg->workdir,
        cfg->isolate_network
    );
    AGEOS_LOG_DEBUG(
        "sandbox resource limits",
        "memory_max=%llu cpu_percent=%u niceness=%d",
        (unsigned long long)cfg->memory_max,
        cfg->cpu_percent,
        cfg->resource_niceness
    );
    uid_t host_uid = getuid();
    gid_t host_gid = getgid();
    uid_t agent_uid = sandbox_agent_uid(host_uid);
    gid_t agent_gid = sandbox_agent_gid(host_gid, agent_uid);
    char sandbox_root_template[] = "/tmp/ageos-root-XXXXXX";
    char *sandbox_root = mkdtemp(sandbox_root_template);
    if (sandbox_root == NULL) {
        return -errno;
    }
    int inference_proxy_enabled =
        cfg->isolate_network &&
        cfg->inference_host != NULL &&
        cfg->inference_host[0] != '\0' &&
        cfg->inference_port > 0 &&
        cfg->sandbox_inference_port > 0;
    int inference_control[2] = {-1, -1};
    pid_t host_proxy_pid = -1;
    if (inference_proxy_enabled) {
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, inference_control) != 0) {
            cleanup_sandbox_root(sandbox_root);
            return -errno;
        }
        host_proxy_pid = fork();
        if (host_proxy_pid < 0) {
            int err = errno;
            close(inference_control[0]);
            close(inference_control[1]);
            cleanup_sandbox_root(sandbox_root);
            return -err;
        }
        if (host_proxy_pid == 0) {
            close(inference_control[1]);
            host_inference_proxy(inference_control[0], cfg->inference_host, cfg->inference_port);
            close(inference_control[0]);
            _exit(0);
        }
        close(inference_control[0]);
    }
    pid_t pid = fork();
    if (pid < 0) {
        int err = errno;
        if (inference_control[1] >= 0) {
            close(inference_control[1]);
        }
        if (host_proxy_pid > 0) {
            kill(host_proxy_pid, SIGTERM);
            waitpid(host_proxy_pid, NULL, 0);
        }
        cleanup_sandbox_root(sandbox_root);
        return -err;
    }
    if (pid == 0) {
        const char *existing_path = getenv("PATH");
        char path_buf[4096];
        if (existing_path != NULL && existing_path[0] != '\0') {
            snprintf(path_buf, sizeof(path_buf), "/usr/local/bin:/opt/ageos/bin:/usr/bin:/bin:%s", existing_path);
        } else {
            snprintf(path_buf, sizeof(path_buf), "/usr/local/bin:/opt/ageos/bin:/usr/bin:/bin");
        }
        setenv("PATH", path_buf, 1);
        setenv("AGEOS_SANDBOX", "1", 1);
        ageos_apply_cgroup_limits(cfg);
        int userns_rc = setup_user_namespace(agent_uid, agent_gid);
        if (userns_rc != 0) {
            AGEOS_LOG_ERROR("failed to create sandbox user namespace", "%s", strerror(-userns_rc));
            _exit(126);
        }
        int unshare_flags = CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWUTS;
        if (cfg->isolate_network) {
            unshare_flags |= CLONE_NEWNET;
        }
        if (unshare(unshare_flags) != 0) {
            AGEOS_LOG_ERROR("failed to create sandbox namespaces", "%s", strerror(errno));
            _exit(126);
        }
        if (cfg->isolate_network) {
            int loopback_rc = setup_loopback();
            if (loopback_rc != 0) {
                AGEOS_LOG_ERROR("failed to enable sandbox loopback", "%s", strerror(-loopback_rc));
                _exit(126);
            }
            if (inference_proxy_enabled) {
                int proxy_rc = start_namespace_inference_proxy(inference_control[1], cfg->sandbox_inference_port);
                if (proxy_rc != 0) {
                    AGEOS_LOG_ERROR("failed to start native inference proxy", "%s", strerror(-proxy_rc));
                    _exit(126);
                }
                close(inference_control[1]);
            }
            if (getenv("AGEOS_NETWORK") == NULL) {
                setenv("AGEOS_NETWORK", "loopback", 1);
            }
        }
        int mounts_rc = setup_mounts(sandbox_root, cfg->workdir, cfg->root_dir);
        if (mounts_rc != 0) {
            AGEOS_LOG_ERROR("failed to create filesystem sandbox", "%s", strerror(-mounts_rc));
            _exit(126);
        }
        const char *writable_dir = (cfg->root_dir != NULL && cfg->root_dir[0] != '\0') ? cfg->root_dir : sandbox_root;
        int env_rc = setup_sandbox_runtime_env(
            writable_dir,
            sandbox_root,
            cfg->workdir,
            cfg->root_dir,
            host_uid,
            agent_uid,
            agent_gid
        );
        if (env_rc != 0) {
            AGEOS_LOG_ERROR("failed to prepare sandbox runtime env", "%s", strerror(-env_rc));
            _exit(126);
        }
        int landlock_rc = ageos_landlock_apply_filesystem(writable_dir);
        if (landlock_rc != 0) {
            AGEOS_LOG_ERROR("failed to apply filesystem policy", "%s", strerror(-landlock_rc));
            _exit(126);
        }
        apply_no_new_privs();
        close_extra_fds();
        if (getenv("AGEOS_ENABLE_SECCOMP") != NULL && strcmp(getenv("AGEOS_ENABLE_SECCOMP"), "1") == 0) {
            apply_seccomp();
        }
        execv(cfg->binary, cfg->argv);
        _exit(127);
    }
    if (inference_control[1] >= 0) {
        close(inference_control[1]);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        int err = errno;
        if (host_proxy_pid > 0) {
            kill(host_proxy_pid, SIGTERM);
            waitpid(host_proxy_pid, NULL, 0);
        }
        cleanup_sandbox_root(sandbox_root);
        return -err;
    }
    if (host_proxy_pid > 0) {
        kill(host_proxy_pid, SIGTERM);
        waitpid(host_proxy_pid, NULL, 0);
    }
    cleanup_sandbox_root(sandbox_root);
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return status;
}
#else
int ageos_sandbox_run(const ageos_sandbox_config *cfg) {
    (void)cfg;
    AGEOS_LOG_ERROR("sandbox is only supported on Linux", "");
    return -ENOTSUP;
}
#endif
