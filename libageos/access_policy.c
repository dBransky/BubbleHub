#define _GNU_SOURCE

#include "ageos/access_policy.h"

#include "ageos/log.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define AGEOS_ACCESS_SCHEMA_VERSION 1U
#define AGEOS_ACCESS_MAX_POLICIES 128U
#define AGEOS_ACCESS_MAX_PENDING 128U
#define AGEOS_ACCESS_JSON_CAPACITY 65536U

typedef struct {
    ageos_access_request request;
    char policy[AGEOS_ACCESS_POLICY_SIZE];
} ageos_access_policy_entry;

typedef struct {
    char id[AGEOS_ACCESS_ID_SIZE];
    ageos_access_request request;
    double created_at;
} ageos_access_pending_entry;

typedef struct {
    char agent_id[AGEOS_ACCESS_AGENT_ID_SIZE];
    ageos_access_policy_entry policies[AGEOS_ACCESS_MAX_POLICIES];
    size_t policy_count;
    ageos_access_pending_entry pending[AGEOS_ACCESS_MAX_PENDING];
    size_t pending_count;
} ageos_access_manifest;

typedef struct {
    char *data;
    size_t len;
    size_t cap;
    int failed;
} ageos_json_builder;

static void copy_field(char *dst, size_t dst_size, const char *src) {
    if (dst == NULL || dst_size == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    snprintf(dst, dst_size, "%s", src);
}

static void normalize_token(char *dst, size_t dst_size, const char *src, int lowercase) {
    if (dst == NULL || dst_size == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    size_t start = 0;
    while (src[start] != '\0' && isspace((unsigned char)src[start])) {
        start++;
    }
    size_t end = strlen(src + start);
    while (end > 0 && isspace((unsigned char)src[start + end - 1])) {
        end--;
    }
    size_t limit = dst_size - 1;
    if (end > limit) {
        end = limit;
    }
    for (size_t i = 0; i < end; i++) {
        unsigned char ch = (unsigned char)src[start + i];
        dst[i] = lowercase ? (char)tolower(ch) : (char)ch;
    }
    dst[end] = '\0';
}

static void normalize_request(ageos_access_request *dst, const ageos_access_request *src) {
    memset(dst, 0, sizeof(*dst));
    if (src == NULL) {
        return;
    }
    normalize_token(dst->kind, sizeof(dst->kind), src->kind, 1);
    normalize_token(dst->subject, sizeof(dst->subject), src->subject, 1);
    normalize_token(dst->method, sizeof(dst->method), src->method, 0);
    normalize_token(dst->path, sizeof(dst->path), src->path, 0);
}

static int is_valid_agent_id(const char *agent_id) {
    if (agent_id == NULL || strncmp(agent_id, "agt-", 4) != 0) {
        return 0;
    }
    size_t len = strlen(agent_id);
    if (len <= 4 || len >= AGEOS_ACCESS_AGENT_ID_SIZE) {
        return 0;
    }
    for (size_t i = 4; i < len; i++) {
        unsigned char ch = (unsigned char)agent_id[i];
        if (!isalnum(ch) && ch != '_' && ch != '-') {
            return 0;
        }
    }
    return 1;
}

static int access_owner_identity(uid_t *uid, gid_t *gid);

static int mkdir_if_missing(const char *path) {
    if (mkdir(path, 0700) == 0) {
        uid_t owner_uid;
        gid_t owner_gid;
        if (access_owner_identity(&owner_uid, &owner_gid)) {
            (void)chown(path, owner_uid, owner_gid);
        }
        return 0;
    }
    if (errno == EEXIST) {
        return 0;
    }
    return -errno;
}

static int parse_owner_id(const char *value, unsigned long *out) {
    if (value == NULL || value[0] == '\0' || out == NULL) {
        return 0;
    }
    char *end = NULL;
    unsigned long parsed = strtoul(value, &end, 10);
    if (end == value || *end != '\0') {
        return 0;
    }
    *out = parsed;
    return 1;
}

static int access_owner_identity(uid_t *uid, gid_t *gid) {
    if (geteuid() != 0 || uid == NULL || gid == NULL) {
        return 0;
    }
    unsigned long env_uid = 0;
    unsigned long env_gid = 0;
    if (parse_owner_id(getenv("AGEOS_HOST_UID"), &env_uid) && parse_owner_id(getenv("AGEOS_HOST_GID"), &env_gid) && env_uid != 0) {
        *uid = (uid_t)env_uid;
        *gid = (gid_t)env_gid;
        return 1;
    }
    if (getuid() == 0) {
        return 0;
    }
    *uid = getuid();
    *gid = getgid();
    return 1;
}

static void ensure_real_user_owner(const char *path) {
    uid_t owner_uid;
    gid_t owner_gid;
    if (path == NULL || path[0] == '\0' || !access_owner_identity(&owner_uid, &owner_gid)) {
        return;
    }
    (void)chown(path, owner_uid, owner_gid);
}

static int mkdir_p(const char *path) {
    if (path == NULL || path[0] == '\0') {
        return -EINVAL;
    }
    char current[PATH_MAX];
    int written = snprintf(current, sizeof(current), "%s", path);
    if (written < 0 || (size_t)written >= sizeof(current)) {
        return -ENAMETOOLONG;
    }
    for (char *cursor = current + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        int rc = mkdir_if_missing(current);
        *cursor = '/';
        if (rc != 0) {
            return rc;
        }
    }
    return mkdir_if_missing(current);
}

static int state_root(char *buffer, size_t buffer_size) {
    const char *explicit_root = getenv("AGEOS_STATE_DIR");
    if (explicit_root != NULL && explicit_root[0] != '\0') {
        int written = snprintf(buffer, buffer_size, "%s", explicit_root);
        if (written < 0 || (size_t)written >= buffer_size) {
            return -ENAMETOOLONG;
        }
        int rc = mkdir_p(buffer);
        if (rc == 0) {
            ensure_real_user_owner(buffer);
        }
        return rc;
    }

    const char *xdg_state = getenv("XDG_STATE_HOME");
    if (xdg_state != NULL && xdg_state[0] != '\0') {
        int written = snprintf(buffer, buffer_size, "%s/ageos", xdg_state);
        if (written < 0 || (size_t)written >= buffer_size) {
            return -ENAMETOOLONG;
        }
        int rc = mkdir_p(buffer);
        if (rc == 0) {
            ensure_real_user_owner(buffer);
        }
        return rc;
    }

    const char *home = getenv("HOME");
    if (home != NULL && home[0] != '\0') {
        int written = snprintf(buffer, buffer_size, "%s/.local/state/ageos", home);
        if (written < 0 || (size_t)written >= buffer_size) {
            return -ENAMETOOLONG;
        }
        int rc = mkdir_p(buffer);
        if (rc == 0) {
            ensure_real_user_owner(buffer);
        }
        return rc;
    }

    int written = snprintf(buffer, buffer_size, "/tmp/ageos-%lu/state", (unsigned long)getuid());
    if (written < 0 || (size_t)written >= buffer_size) {
        return -ENAMETOOLONG;
    }
    int rc = mkdir_p(buffer);
    if (rc == 0) {
        ensure_real_user_owner(buffer);
    }
    return rc;
}

int ageos_access_manifest_path(const char *agent_id, char *buffer, size_t buffer_size) {
    if (!is_valid_agent_id(agent_id) || buffer == NULL || buffer_size == 0) {
        return -EINVAL;
    }
    char root[PATH_MAX];
    int rc = state_root(root, sizeof(root));
    if (rc != 0) {
        return rc;
    }
    char sandboxes_dir[PATH_MAX];
    int written = snprintf(sandboxes_dir, sizeof(sandboxes_dir), "%s/sandboxes", root);
    if (written < 0 || (size_t)written >= sizeof(sandboxes_dir)) {
        return -ENAMETOOLONG;
    }
    char dir[PATH_MAX];
    written = snprintf(dir, sizeof(dir), "%s/%s", sandboxes_dir, agent_id);
    if (written < 0 || (size_t)written >= sizeof(dir)) {
        return -ENAMETOOLONG;
    }
    rc = mkdir_p(dir);
    if (rc != 0) {
        return rc;
    }
    ensure_real_user_owner(sandboxes_dir);
    ensure_real_user_owner(dir);
    written = snprintf(buffer, buffer_size, "%s/access-manifest.json", dir);
    if (written < 0 || (size_t)written >= buffer_size) {
        return -ENAMETOOLONG;
    }
    return 0;
}

static void manifest_init(ageos_access_manifest *manifest, const char *agent_id) {
    memset(manifest, 0, sizeof(*manifest));
    copy_field(manifest->agent_id, sizeof(manifest->agent_id), agent_id);
}

static char *read_file(const char *path) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return NULL;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size < 0 || st.st_size > (off_t)(AGEOS_ACCESS_JSON_CAPACITY * 4)) {
        close(fd);
        return NULL;
    }
    size_t size = (size_t)st.st_size;
    char *buffer = malloc(size + 1);
    if (buffer == NULL) {
        close(fd);
        return NULL;
    }
    size_t offset = 0;
    while (offset < size) {
        ssize_t read_count = read(fd, buffer + offset, size - offset);
        if (read_count < 0) {
            if (errno == EINTR) {
                continue;
            }
            free(buffer);
            close(fd);
            return NULL;
        }
        if (read_count == 0) {
            break;
        }
        offset += (size_t)read_count;
    }
    close(fd);
    buffer[offset] = '\0';
    return buffer;
}

static const char *skip_ws(const char *cursor) {
    while (cursor != NULL && *cursor != '\0' && isspace((unsigned char)*cursor)) {
        cursor++;
    }
    return cursor;
}

static int json_get_string(const char *object, const char *key, char *buffer, size_t buffer_size) {
    if (object == NULL || key == NULL || buffer == NULL || buffer_size == 0) {
        return 0;
    }
    char needle[96];
    int written = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (written < 0 || (size_t)written >= sizeof(needle)) {
        return 0;
    }
    const char *pos = strstr(object, needle);
    if (pos == NULL) {
        return 0;
    }
    pos = strchr(pos + strlen(needle), ':');
    if (pos == NULL) {
        return 0;
    }
    pos = skip_ws(pos + 1);
    if (*pos != '"') {
        return 0;
    }
    pos++;
    size_t out = 0;
    while (*pos != '\0' && *pos != '"' && out + 1 < buffer_size) {
        if (*pos == '\\' && pos[1] != '\0') {
            pos++;
        }
        buffer[out++] = *pos++;
    }
    buffer[out] = '\0';
    return *pos == '"';
}

static double json_get_number(const char *object, const char *key) {
    char needle[96];
    int written = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (written < 0 || (size_t)written >= sizeof(needle)) {
        return 0.0;
    }
    const char *pos = strstr(object, needle);
    if (pos == NULL) {
        return 0.0;
    }
    pos = strchr(pos + strlen(needle), ':');
    if (pos == NULL) {
        return 0.0;
    }
    return strtod(skip_ws(pos + 1), NULL);
}

static const char *find_array(const char *json, const char *name, const char **array_end) {
    char needle[96];
    int written = snprintf(needle, sizeof(needle), "\"%s\"", name);
    if (written < 0 || (size_t)written >= sizeof(needle)) {
        return NULL;
    }
    const char *pos = strstr(json, needle);
    if (pos == NULL) {
        return NULL;
    }
    pos = strchr(pos + strlen(needle), '[');
    if (pos == NULL) {
        return NULL;
    }
    int depth = 0;
    int in_string = 0;
    int escaped = 0;
    for (const char *cursor = pos; *cursor != '\0'; cursor++) {
        if (in_string) {
            if (escaped) {
                escaped = 0;
            } else if (*cursor == '\\') {
                escaped = 1;
            } else if (*cursor == '"') {
                in_string = 0;
            }
            continue;
        }
        if (*cursor == '"') {
            in_string = 1;
        } else if (*cursor == '[') {
            depth++;
        } else if (*cursor == ']') {
            depth--;
            if (depth == 0) {
                *array_end = cursor;
                return pos + 1;
            }
        }
    }
    return NULL;
}

static const char *next_object(const char *cursor, const char *end, const char **object_end) {
    while (cursor < end && *cursor != '{') {
        cursor++;
    }
    if (cursor >= end) {
        return NULL;
    }
    int depth = 0;
    int in_string = 0;
    int escaped = 0;
    for (const char *pos = cursor; pos < end; pos++) {
        if (in_string) {
            if (escaped) {
                escaped = 0;
            } else if (*pos == '\\') {
                escaped = 1;
            } else if (*pos == '"') {
                in_string = 0;
            }
            continue;
        }
        if (*pos == '"') {
            in_string = 1;
        } else if (*pos == '{') {
            depth++;
        } else if (*pos == '}') {
            depth--;
            if (depth == 0) {
                *object_end = pos + 1;
                return cursor;
            }
        }
    }
    return NULL;
}

static void parse_request_object(const char *object, ageos_access_request *request) {
    char value[AGEOS_ACCESS_PATH_SIZE];
    ageos_access_request raw;
    memset(&raw, 0, sizeof(raw));
    if (json_get_string(object, "kind", value, sizeof(value))) {
        copy_field(raw.kind, sizeof(raw.kind), value);
    }
    if (json_get_string(object, "subject", value, sizeof(value))) {
        copy_field(raw.subject, sizeof(raw.subject), value);
    }
    if (json_get_string(object, "method", value, sizeof(value))) {
        copy_field(raw.method, sizeof(raw.method), value);
    }
    if (json_get_string(object, "path", value, sizeof(value))) {
        copy_field(raw.path, sizeof(raw.path), value);
    }
    normalize_request(request, &raw);
}

static void parse_policies(const char *json, ageos_access_manifest *manifest) {
    const char *end = NULL;
    const char *cursor = find_array(json, "policies", &end);
    while (cursor != NULL && cursor < end && manifest->policy_count < AGEOS_ACCESS_MAX_POLICIES) {
        const char *object_end = NULL;
        const char *object = next_object(cursor, end, &object_end);
        if (object == NULL) {
            break;
        }
        ageos_access_policy_entry *entry = &manifest->policies[manifest->policy_count];
        parse_request_object(object, &entry->request);
        json_get_string(object, "policy", entry->policy, sizeof(entry->policy));
        normalize_token(entry->policy, sizeof(entry->policy), entry->policy, 1);
        if (entry->request.kind[0] != '\0' && entry->request.subject[0] != '\0' &&
            (strcmp(entry->policy, "always") == 0 || strcmp(entry->policy, "never") == 0 || strcmp(entry->policy, "ask") == 0)) {
            manifest->policy_count++;
        }
        cursor = object_end;
    }
}

static void parse_pending(const char *json, ageos_access_manifest *manifest) {
    const char *end = NULL;
    const char *cursor = find_array(json, "pending", &end);
    while (cursor != NULL && cursor < end && manifest->pending_count < AGEOS_ACCESS_MAX_PENDING) {
        const char *object_end = NULL;
        const char *object = next_object(cursor, end, &object_end);
        if (object == NULL) {
            break;
        }
        ageos_access_pending_entry *entry = &manifest->pending[manifest->pending_count];
        parse_request_object(object, &entry->request);
        json_get_string(object, "id", entry->id, sizeof(entry->id));
        entry->created_at = json_get_number(object, "created_at");
        if (entry->request.kind[0] != '\0' && entry->request.subject[0] != '\0') {
            manifest->pending_count++;
        }
        cursor = object_end;
    }
}

static int load_manifest(const char *agent_id, ageos_access_manifest *manifest) {
    manifest_init(manifest, agent_id);
    char path[PATH_MAX];
    int rc = ageos_access_manifest_path(agent_id, path, sizeof(path));
    if (rc != 0) {
        return rc;
    }
    char *json = read_file(path);
    if (json == NULL) {
        return errno == ENOENT ? 0 : 0;
    }
    char parsed_agent_id[AGEOS_ACCESS_AGENT_ID_SIZE];
    if (json_get_string(json, "agent_id", parsed_agent_id, sizeof(parsed_agent_id)) && strcmp(parsed_agent_id, agent_id) != 0) {
        free(json);
        return -EINVAL;
    }
    parse_policies(json, manifest);
    parse_pending(json, manifest);
    free(json);
    return 0;
}

static void json_builder_init(ageos_json_builder *builder, char *data, size_t cap) {
    builder->data = data;
    builder->len = 0;
    builder->cap = cap;
    builder->failed = 0;
    if (cap > 0) {
        data[0] = '\0';
    }
}

static void json_append(ageos_json_builder *builder, const char *fmt, ...) {
    if (builder->failed || builder->len >= builder->cap) {
        builder->failed = 1;
        return;
    }
    va_list args;
    va_start(args, fmt);
    int written = vsnprintf(builder->data + builder->len, builder->cap - builder->len, fmt, args);
    va_end(args);
    if (written < 0 || (size_t)written >= builder->cap - builder->len) {
        builder->failed = 1;
        return;
    }
    builder->len += (size_t)written;
}

static void json_append_escaped(ageos_json_builder *builder, const char *value) {
    json_append(builder, "\"");
    if (value != NULL) {
        for (const char *cursor = value; *cursor != '\0' && !builder->failed; cursor++) {
            unsigned char ch = (unsigned char)*cursor;
            if (ch == '"' || ch == '\\') {
                json_append(builder, "\\%c", ch);
            } else if (ch == '\n') {
                json_append(builder, "\\n");
            } else if (ch == '\r') {
                json_append(builder, "\\r");
            } else if (ch == '\t') {
                json_append(builder, "\\t");
            } else if (ch < 0x20) {
                json_append(builder, "\\u%04x", ch);
            } else {
                json_append(builder, "%c", ch);
            }
        }
    }
    json_append(builder, "\"");
}

static void json_append_request(ageos_json_builder *builder, const ageos_access_request *request) {
    json_append(builder, "\"kind\":");
    json_append_escaped(builder, request->kind);
    json_append(builder, ",\"subject\":");
    json_append_escaped(builder, request->subject);
    json_append(builder, ",\"method\":");
    json_append_escaped(builder, request->method);
    json_append(builder, ",\"path\":");
    json_append_escaped(builder, request->path);
}

static char *manifest_to_json(const ageos_access_manifest *manifest) {
    char *json = malloc(AGEOS_ACCESS_JSON_CAPACITY);
    if (json == NULL) {
        errno = ENOMEM;
        return NULL;
    }
    ageos_json_builder builder;
    json_builder_init(&builder, json, AGEOS_ACCESS_JSON_CAPACITY);
    json_append(&builder, "{\n  \"version\": %u,\n  \"agent_id\": ", AGEOS_ACCESS_SCHEMA_VERSION);
    json_append_escaped(&builder, manifest->agent_id);
    json_append(&builder, ",\n  \"policies\": [");
    for (size_t i = 0; i < manifest->policy_count; i++) {
        const ageos_access_policy_entry *entry = &manifest->policies[i];
        json_append(&builder, "%s\n    {", i == 0 ? "" : ",");
        json_append_request(&builder, &entry->request);
        json_append(&builder, ",\"policy\":");
        json_append_escaped(&builder, entry->policy);
        json_append(&builder, "}");
    }
    json_append(&builder, "\n  ],\n  \"pending\": [");
    for (size_t i = 0; i < manifest->pending_count; i++) {
        const ageos_access_pending_entry *entry = &manifest->pending[i];
        json_append(&builder, "%s\n    {\"id\":", i == 0 ? "" : ",");
        json_append_escaped(&builder, entry->id);
        json_append(&builder, ",");
        json_append_request(&builder, &entry->request);
        json_append(&builder, ",\"created_at\":%.0f}", entry->created_at);
    }
    json_append(&builder, "\n  ]\n}\n");
    if (builder.failed) {
        free(json);
        errno = ENOSPC;
        return NULL;
    }
    return json;
}

static int write_manifest(const ageos_access_manifest *manifest) {
    char path[PATH_MAX];
    int rc = ageos_access_manifest_path(manifest->agent_id, path, sizeof(path));
    if (rc != 0) {
        return rc;
    }
    char *json = manifest_to_json(manifest);
    if (json == NULL) {
        return -errno;
    }

    char tmp_path[PATH_MAX];
    int written = snprintf(tmp_path, sizeof(tmp_path), "%s.tmp.%ld", path, (long)getpid());
    if (written < 0 || (size_t)written >= sizeof(tmp_path)) {
        free(json);
        return -ENAMETOOLONG;
    }
    int fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) {
        int err = errno;
        free(json);
        return -err;
    }
    uid_t owner_uid;
    gid_t owner_gid;
    if (access_owner_identity(&owner_uid, &owner_gid)) {
        (void)fchown(fd, owner_uid, owner_gid);
    }
    size_t json_len = strlen(json);
    size_t offset = 0;
    while (offset < json_len) {
        ssize_t out = write(fd, json + offset, json_len - offset);
        if (out < 0) {
            if (errno == EINTR) {
                continue;
            }
            int err = errno;
            close(fd);
            unlink(tmp_path);
            free(json);
            return -err;
        }
        offset += (size_t)out;
    }
    if (fsync(fd) != 0) {
        int err = errno;
        close(fd);
        unlink(tmp_path);
        free(json);
        return -err;
    }
    if (close(fd) != 0) {
        int err = errno;
        unlink(tmp_path);
        free(json);
        return -err;
    }
    free(json);
    if (rename(tmp_path, path) != 0) {
        int err = errno;
        unlink(tmp_path);
        return -err;
    }
    ensure_real_user_owner(path);
    return 0;
}

static unsigned long request_hash(const ageos_access_request *request) {
    unsigned long hash = 5381;
    const char *parts[] = {request->kind, request->subject, request->method, request->path};
    for (size_t i = 0; i < sizeof(parts) / sizeof(parts[0]); i++) {
        for (const unsigned char *p = (const unsigned char *)parts[i]; *p != '\0'; p++) {
            hash = ((hash << 5) + hash) + *p;
        }
        hash = ((hash << 5) + hash) + '|';
    }
    return hash;
}

static int request_equals(const ageos_access_request *left, const ageos_access_request *right) {
    return strcmp(left->kind, right->kind) == 0 &&
           strcmp(left->subject, right->subject) == 0 &&
           strcmp(left->method, right->method) == 0 &&
           strcmp(left->path, right->path) == 0;
}

static int subject_matches(const char *policy_subject, const char *subject) {
    if (strcmp(policy_subject, subject) == 0 || strcmp(policy_subject, "*") == 0) {
        return 1;
    }
    if (strncmp(policy_subject, "*.", 2) != 0) {
        return 0;
    }
    const char *suffix = policy_subject + 1;
    size_t subject_len = strlen(subject);
    size_t suffix_len = strlen(suffix);
    return subject_len > suffix_len && strcmp(subject + subject_len - suffix_len, suffix) == 0;
}

static int method_matches(const char *policy_method, const char *method) {
    return policy_method[0] == '\0' || strcmp(policy_method, "*") == 0 || strcmp(policy_method, method) == 0;
}

static int path_matches(const char *policy_path, const char *path) {
    return policy_path[0] == '\0' || strcmp(policy_path, "*") == 0 || strcmp(policy_path, path) == 0;
}

static int request_matches_rule(const ageos_access_request *rule, const ageos_access_request *request) {
    return strcmp(rule->kind, request->kind) == 0 &&
           subject_matches(rule->subject, request->subject) &&
           method_matches(rule->method, request->method) &&
           path_matches(rule->path, request->path);
}

static ageos_access_policy_entry *find_policy(ageos_access_manifest *manifest, const ageos_access_request *request) {
    ageos_access_policy_entry *best = NULL;
    int best_score = -1;
    for (size_t i = 0; i < manifest->policy_count; i++) {
        ageos_access_policy_entry *entry = &manifest->policies[i];
        if (!request_matches_rule(&entry->request, request)) {
            continue;
        }
        int score = 0;
        if (strcmp(entry->request.subject, request->subject) == 0) {
            score += 4;
        }
        if (strcmp(entry->request.method, request->method) == 0) {
            score += 2;
        }
        if (strcmp(entry->request.path, request->path) == 0) {
            score += 1;
        }
        if (score > best_score) {
            best = entry;
            best_score = score;
        }
    }
    return best;
}

static int remove_pending(ageos_access_manifest *manifest, const ageos_access_request *request) {
    size_t out = 0;
    int removed = 0;
    for (size_t i = 0; i < manifest->pending_count; i++) {
        if (request_equals(&manifest->pending[i].request, request) || request_matches_rule(request, &manifest->pending[i].request)) {
            removed = 1;
            continue;
        }
        if (out != i) {
            manifest->pending[out] = manifest->pending[i];
        }
        out++;
    }
    manifest->pending_count = out;
    return removed;
}

static int add_pending(ageos_access_manifest *manifest, const ageos_access_request *request) {
    for (size_t i = 0; i < manifest->pending_count; i++) {
        if (request_equals(&manifest->pending[i].request, request)) {
            return 0;
        }
    }
    if (manifest->pending_count >= AGEOS_ACCESS_MAX_PENDING) {
        return -ENOSPC;
    }
    ageos_access_pending_entry *entry = &manifest->pending[manifest->pending_count++];
    snprintf(entry->id, sizeof(entry->id), "req-%lx", request_hash(request));
    entry->request = *request;
    entry->created_at = (double)time(NULL);
    return 0;
}

static int is_policy_value(const char *policy) {
    return strcmp(policy, "always") == 0 || strcmp(policy, "never") == 0 || strcmp(policy, "ask") == 0;
}

int ageos_access_apply_policy(const char *agent_id, const ageos_access_request *request, const char *policy) {
    if (!is_valid_agent_id(agent_id) || request == NULL || policy == NULL) {
        return -EINVAL;
    }
    ageos_access_request normalized;
    normalize_request(&normalized, request);
    char normalized_policy[AGEOS_ACCESS_POLICY_SIZE];
    normalize_token(normalized_policy, sizeof(normalized_policy), policy, 1);
    if (normalized.kind[0] == '\0' || normalized.subject[0] == '\0' || !is_policy_value(normalized_policy)) {
        return -EINVAL;
    }
    ageos_access_manifest manifest;
    int rc = load_manifest(agent_id, &manifest);
    if (rc != 0) {
        return rc;
    }
    ageos_access_policy_entry *entry = NULL;
    for (size_t i = 0; i < manifest.policy_count; i++) {
        if (request_equals(&manifest.policies[i].request, &normalized)) {
            entry = &manifest.policies[i];
            break;
        }
    }
    if (entry == NULL) {
        if (manifest.policy_count >= AGEOS_ACCESS_MAX_POLICIES) {
            return -ENOSPC;
        }
        entry = &manifest.policies[manifest.policy_count++];
        entry->request = normalized;
    }
    copy_field(entry->policy, sizeof(entry->policy), normalized_policy);
    remove_pending(&manifest, &normalized);
    return write_manifest(&manifest);
}

char *ageos_access_manifest_json(const char *agent_id) {
    if (!is_valid_agent_id(agent_id)) {
        return NULL;
    }
    ageos_access_manifest manifest;
    int rc = load_manifest(agent_id, &manifest);
    if (rc != 0) {
        errno = -rc;
        return NULL;
    }
    return manifest_to_json(&manifest);
}

int ageos_access_evaluate(
    const char *agent_id,
    const ageos_access_request *request,
    int allow_prompt,
    ageos_access_decision *decision) {
    (void)allow_prompt;
    if (!is_valid_agent_id(agent_id) || request == NULL || decision == NULL) {
        return -EINVAL;
    }
    *decision = AGEOS_ACCESS_DECISION_DENY;
    ageos_access_request normalized;
    normalize_request(&normalized, request);
    if (normalized.kind[0] == '\0' || normalized.subject[0] == '\0') {
        return -EINVAL;
    }
    ageos_access_manifest manifest;
    int rc = load_manifest(agent_id, &manifest);
    if (rc != 0) {
        return rc;
    }
    ageos_access_policy_entry *entry = find_policy(&manifest, &normalized);
    if (entry != NULL && strcmp(entry->policy, "always") == 0) {
        *decision = AGEOS_ACCESS_DECISION_APPROVE;
        return 0;
    }
    if (entry != NULL && strcmp(entry->policy, "never") == 0) {
        *decision = AGEOS_ACCESS_DECISION_DENY;
        return 0;
    }

    rc = add_pending(&manifest, &normalized);
    if (rc != 0) {
        return rc;
    }
    rc = write_manifest(&manifest);
    if (rc != 0) {
        return rc;
    }
    AGEOS_LOG_INFO(
        "access request pending",
        "agent_id=%s kind=%s subject=%s method=%s path=%s",
        agent_id,
        normalized.kind,
        normalized.subject,
        normalized.method,
        normalized.path);
    *decision = AGEOS_ACCESS_DECISION_DENY;
    return 0;
}

int ageos_access_needs_prompt(
    const char *agent_id,
    const ageos_access_request *request,
    int *needs_prompt) {
    if (!is_valid_agent_id(agent_id) || request == NULL || needs_prompt == NULL) {
        return -EINVAL;
    }
    *needs_prompt = 0;
    ageos_access_request normalized;
    normalize_request(&normalized, request);
    if (normalized.kind[0] == '\0' || normalized.subject[0] == '\0') {
        return -EINVAL;
    }
    ageos_access_manifest manifest;
    int rc = load_manifest(agent_id, &manifest);
    if (rc != 0) {
        return rc;
    }
    ageos_access_policy_entry *entry = find_policy(&manifest, &normalized);
    *needs_prompt = entry == NULL || strcmp(entry->policy, "ask") == 0;
    return 0;
}

static void append_pending_entry_json(
    ageos_json_builder *builder,
    const char *agent_id,
    const ageos_access_pending_entry *entry,
    int *first) {
    json_append(builder, "%s{\"agent_id\":", *first ? "" : ",");
    *first = 0;
    json_append_escaped(builder, agent_id);
    json_append(builder, ",\"id\":");
    json_append_escaped(builder, entry->id);
    json_append(builder, ",");
    json_append_request(builder, &entry->request);
    json_append(builder, ",\"created_at\":%.0f}", entry->created_at);
}

char *ageos_access_pending_json(void) {
    char root[PATH_MAX];
    int rc = state_root(root, sizeof(root));
    if (rc != 0) {
        return NULL;
    }
    char sandboxes_dir[PATH_MAX];
    int written = snprintf(sandboxes_dir, sizeof(sandboxes_dir), "%s/sandboxes", root);
    if (written < 0 || (size_t)written >= sizeof(sandboxes_dir)) {
        return NULL;
    }
    if (mkdir_p(sandboxes_dir) != 0) {
        return NULL;
    }
    char *json = malloc(AGEOS_ACCESS_JSON_CAPACITY);
    if (json == NULL) {
        return NULL;
    }
    ageos_json_builder builder;
    json_builder_init(&builder, json, AGEOS_ACCESS_JSON_CAPACITY);
    json_append(&builder, "[");
    int first = 1;
    DIR *dir = opendir(sandboxes_dir);
    if (dir != NULL) {
        struct dirent *entry;
        while ((entry = readdir(dir)) != NULL) {
            if (!is_valid_agent_id(entry->d_name)) {
                continue;
            }
            ageos_access_manifest manifest;
            if (load_manifest(entry->d_name, &manifest) != 0) {
                continue;
            }
            for (size_t i = 0; i < manifest.pending_count; i++) {
                append_pending_entry_json(&builder, manifest.agent_id, &manifest.pending[i], &first);
            }
        }
        closedir(dir);
    }
    json_append(&builder, "]\n");
    if (builder.failed) {
        free(json);
        return NULL;
    }
    return json;
}

void ageos_access_free_string(char *value) {
    free(value);
}
