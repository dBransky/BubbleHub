#include "bubblehub/scheduler.h"

#include "bubblehub/hw.h"
#include "bubblehub/log.h"

#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define BUBBLEHUB_SCHED_MAGIC 0x4147534348443031ULL
#define BUBBLEHUB_SCHED_VERSION 2U
#define BUBBLEHUB_MAX_AGENTS 64
#define BUBBLEHUB_MAX_MODELS 32
#define BUBBLEHUB_MAX_QUEUE 64
#define BUBBLEHUB_FIELD_SMALL 64
#define BUBBLEHUB_FIELD_MEDIUM 256
#define BUBBLEHUB_FIELD_LARGE 512
#define BUBBLEHUB_JSON_CAPACITY 65536
#define BUBBLEHUB_RAM_LOW_WATERMARK_PERCENT 20.0
#define BUBBLEHUB_RAM_NO_ADMIT_WATERMARK_PERCENT 8.0
#define BUBBLEHUB_GPU_RESERVED_VRAM_PERCENT 10.0
#define BUBBLEHUB_AGENT_UID_BASE 60000U
#define BUBBLEHUB_AGENT_UID_END 64000U

typedef struct {
    int active;
    char agent_id[BUBBLEHUB_FIELD_SMALL];
    int64_t pid;
    char binary[BUBBLEHUB_FIELD_LARGE];
    int niceness;
    char status[BUBBLEHUB_FIELD_SMALL];
    char specialty[BUBBLEHUB_FIELD_SMALL];
    double registered_at;
} bubblehub_agent_record;

typedef struct {
    int active;
    char name[BUBBLEHUB_FIELD_SMALL];
    char specialty[BUBBLEHUB_FIELD_SMALL];
    char backend[BUBBLEHUB_FIELD_SMALL];
    double ram_gb;
    double vram_gb;
    int64_t pid;
    int port;
    int refcount;
    double loaded_at;
    double last_used;
} bubblehub_model_record;

typedef struct {
    int active;
    char job_id[BUBBLEHUB_FIELD_SMALL];
    char kind[BUBBLEHUB_FIELD_SMALL];
    char specialty[BUBBLEHUB_FIELD_SMALL];
    char model_name[BUBBLEHUB_FIELD_SMALL];
    int niceness;
    double created_at;
    char reason[BUBBLEHUB_FIELD_MEDIUM];
} bubblehub_queue_record;

typedef struct {
    uint64_t magic;
    uint32_t version;
    uint32_t reserved;
    double ram_limit_gb;
    double vram_limit_gb;
    bubblehub_agent_record agents[BUBBLEHUB_MAX_AGENTS];
    bubblehub_model_record models[BUBBLEHUB_MAX_MODELS];
    bubblehub_queue_record queue[BUBBLEHUB_MAX_QUEUE];
} bubblehub_scheduler_state;

typedef struct {
    int fd;
    bubblehub_scheduler_state state;
} bubblehub_locked_state;

typedef struct {
    char *data;
    size_t len;
    size_t cap;
    int failed;
} bubblehub_json_builder;

static double bubblehub_now_seconds(void) {
    return (double)time(NULL);
}

static void copy_field(char *dst, size_t dst_size, const char *src) {
    if (dst_size == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    snprintf(dst, dst_size, "%s", src);
}

static int mkdir_if_needed(const char *path) {
    if (mkdir(path, 0700) == 0 || errno == EEXIST) {
        return 0;
    }
    return -1;
}

static int running_in_sandbox_user_namespace(void) {
    uid_t effective_uid = geteuid();
    if (effective_uid >= BUBBLEHUB_AGENT_UID_BASE && effective_uid < BUBBLEHUB_AGENT_UID_END) {
        return 1;
    }
    FILE *handle = fopen("/proc/self/uid_map", "r");
    if (handle == NULL) {
        if (errno == EACCES && geteuid() == 0) {
            return 1;
        }
        return 0;
    }
    unsigned long inside_uid = 0;
    unsigned long outside_uid = 0;
    unsigned long count = 0;
    int sandboxed = 0;
    while (fscanf(handle, "%lu %lu %lu", &inside_uid, &outside_uid, &count) == 3) {
        if (outside_uid != inside_uid && count == 1) {
            sandboxed = 1;
            break;
        }
    }
    fclose(handle);
    return sandboxed;
}

static int sandbox_scheduler_path(char *buffer, size_t buffer_size) {
    char cwd[512];
    if (getcwd(cwd, sizeof(cwd)) == NULL) {
        return -1;
    }
    char dir[1024];
    snprintf(dir, sizeof(dir), "%s/.bubblehub", cwd);
    if (mkdir_if_needed(dir) != 0) {
        return -1;
    }
    snprintf(buffer, buffer_size, "%s/scheduler.state", dir);
    return 0;
}

static int scheduler_path(char *buffer, size_t buffer_size) {
    const char *explicit_path = getenv("BUBBLEHUB_SCHEDULER_STATE");
    if (explicit_path != NULL && explicit_path[0] != '\0') {
        snprintf(buffer, buffer_size, "%s", explicit_path);
        return 0;
    }

    if (running_in_sandbox_user_namespace()) {
        return sandbox_scheduler_path(buffer, buffer_size);
    }

    const char *runtime_dir = getenv("XDG_RUNTIME_DIR");
    if (runtime_dir != NULL && runtime_dir[0] != '\0') {
        char dir[512];
        snprintf(dir, sizeof(dir), "%s/bubblehub", runtime_dir);
        if (mkdir_if_needed(dir) != 0) {
            return -1;
        }
        snprintf(buffer, buffer_size, "%s/scheduler.state", dir);
        return 0;
    }

    char dir[512];
    snprintf(dir, sizeof(dir), "/tmp/bubblehub-%lu", (unsigned long)getuid());
    if (mkdir_if_needed(dir) != 0) {
        return -1;
    }
    snprintf(buffer, buffer_size, "%s/scheduler.state", dir);
    return 0;
}

static void init_state(bubblehub_scheduler_state *state) {
    memset(state, 0, sizeof(*state));
    state->magic = BUBBLEHUB_SCHED_MAGIC;
    state->version = BUBBLEHUB_SCHED_VERSION;
}

static int lock_state(bubblehub_locked_state *locked) {
    char path[1024];
    if (scheduler_path(path, sizeof(path)) != 0) {
        return -1;
    }
    int fd = open(path, O_RDWR | O_CREAT, 0600);
    if (fd < 0) {
        return -1;
    }
    if (flock(fd, LOCK_EX) != 0) {
        close(fd);
        return -1;
    }

    bubblehub_scheduler_state state;
    ssize_t bytes = pread(fd, &state, sizeof(state), 0);
    if (bytes != (ssize_t)sizeof(state) || state.magic != BUBBLEHUB_SCHED_MAGIC || state.version != BUBBLEHUB_SCHED_VERSION) {
        init_state(&state);
        if (ftruncate(fd, (off_t)sizeof(state)) != 0) {
            flock(fd, LOCK_UN);
            close(fd);
            return -1;
        }
        if (pwrite(fd, &state, sizeof(state), 0) != (ssize_t)sizeof(state)) {
            flock(fd, LOCK_UN);
            close(fd);
            return -1;
        }
    }

    locked->fd = fd;
    locked->state = state;
    return 0;
}

static int unlock_state(bubblehub_locked_state *locked, int write_state) {
    int result = 0;
    if (write_state) {
        if (pwrite(locked->fd, &locked->state, sizeof(locked->state), 0) != (ssize_t)sizeof(locked->state)) {
            result = -1;
        }
        fsync(locked->fd);
    }
    flock(locked->fd, LOCK_UN);
    close(locked->fd);
    return result;
}

static int find_agent(bubblehub_scheduler_state *state, const char *agent_id) {
    for (int i = 0; i < BUBBLEHUB_MAX_AGENTS; i++) {
        if (state->agents[i].active && strcmp(state->agents[i].agent_id, agent_id) == 0) {
            return i;
        }
    }
    return -1;
}

static int find_model(bubblehub_scheduler_state *state, const char *name) {
    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        if (state->models[i].active && strcmp(state->models[i].name, name) == 0) {
            return i;
        }
    }
    return -1;
}

static int find_free_agent(bubblehub_scheduler_state *state) {
    for (int i = 0; i < BUBBLEHUB_MAX_AGENTS; i++) {
        if (!state->agents[i].active) {
            return i;
        }
    }
    return -1;
}

static int find_free_model(bubblehub_scheduler_state *state) {
    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        if (!state->models[i].active) {
            return i;
        }
    }
    return -1;
}

static int find_free_queue(bubblehub_scheduler_state *state) {
    for (int i = 0; i < BUBBLEHUB_MAX_QUEUE; i++) {
        if (!state->queue[i].active) {
            return i;
        }
    }
    return -1;
}

static double committed_ram_gb(bubblehub_scheduler_state *state) {
    double total = 0.0;
    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        if (state->models[i].active) {
            total += state->models[i].ram_gb;
        }
    }
    return total;
}

static double committed_vram_gb(bubblehub_scheduler_state *state) {
    double total = 0.0;
    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        if (state->models[i].active) {
            total += state->models[i].vram_gb;
        }
    }
    return total;
}

static double effective_ram_limit_gb(bubblehub_scheduler_state *state) {
    if (state->ram_limit_gb > 0.0) {
        return state->ram_limit_gb;
    }
    double total_gb = (double)bubblehub_hw_total_ram_bytes() / 1073741824.0;
    return total_gb > 1.0 ? total_gb : 1.0;
}

static double effective_vram_limit_gb(bubblehub_scheduler_state *state) {
    if (state->vram_limit_gb > 0.0) {
        return state->vram_limit_gb;
    }
    return (double)bubblehub_hw_vram_bytes() / 1073741824.0;
}

static const char *ram_state(bubblehub_scheduler_state *state, double requested_ram_gb) {
    double total_gb = effective_ram_limit_gb(state);
    double remaining_gb = total_gb - committed_ram_gb(state) - requested_ram_gb;
    double remaining_percent = (remaining_gb / total_gb) * 100.0;
    if (remaining_percent < BUBBLEHUB_RAM_NO_ADMIT_WATERMARK_PERCENT) {
        return "no_ram";
    }
    if (remaining_percent < BUBBLEHUB_RAM_LOW_WATERMARK_PERCENT) {
        return "low";
    }
    return "available";
}

static void terminate_model_process(bubblehub_model_record *record) {
    if (record->pid > 0) {
        kill((pid_t)record->pid, SIGTERM);
    }
}

static void evict_model_at(bubblehub_scheduler_state *state, int index) {
    if (index < 0 || index >= BUBBLEHUB_MAX_MODELS || !state->models[index].active) {
        return;
    }
    terminate_model_process(&state->models[index]);
    memset(&state->models[index], 0, sizeof(state->models[index]));
}

static int find_lru_idle_model(bubblehub_scheduler_state *state, const char *except_name) {
    int index = -1;
    double oldest = 0.0;
    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        bubblehub_model_record *item = &state->models[i];
        if (!item->active || item->refcount > 0) {
            continue;
        }
        if (except_name != NULL && strcmp(item->name, except_name) == 0) {
            continue;
        }
        if (index < 0 || item->last_used < oldest) {
            index = i;
            oldest = item->last_used;
        }
    }
    return index;
}

static int has_capacity_for(bubblehub_scheduler_state *state, double requested_ram_gb, double requested_vram_gb) {
    if (committed_ram_gb(state) + requested_ram_gb > effective_ram_limit_gb(state)) {
        return 0;
    }
    double vram_limit_gb = effective_vram_limit_gb(state);
    if (requested_vram_gb > 0.0 && vram_limit_gb > 0.0 && committed_vram_gb(state) + requested_vram_gb > vram_limit_gb) {
        return 0;
    }
    return 1;
}

static int evict_idle_until_fits(bubblehub_scheduler_state *state, const char *model_name, double requested_ram_gb, double requested_vram_gb) {
    while (!has_capacity_for(state, requested_ram_gb, requested_vram_gb)) {
        int index = find_lru_idle_model(state, model_name);
        if (index < 0) {
            return 0;
        }
        evict_model_at(state, index);
    }
    return 1;
}

static int add_queue_item_locked(
    bubblehub_scheduler_state *state,
    const char *job_id,
    const char *kind,
    const char *specialty,
    const char *model_name,
    int niceness,
    const char *reason) {
    int index = find_free_queue(state);
    if (index < 0) {
        index = 0;
    }
    bubblehub_queue_record *record = &state->queue[index];
    memset(record, 0, sizeof(*record));
    record->active = 1;
    copy_field(record->job_id, sizeof(record->job_id), job_id);
    copy_field(record->kind, sizeof(record->kind), kind);
    copy_field(record->specialty, sizeof(record->specialty), specialty);
    copy_field(record->model_name, sizeof(record->model_name), model_name);
    record->niceness = niceness;
    record->created_at = bubblehub_now_seconds();
    copy_field(record->reason, sizeof(record->reason), reason);
    return 0;
}

int bubblehub_scheduler_configure_limits(double ram_limit_gb, double vram_limit_gb) {
    bubblehub_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }
    locked.state.ram_limit_gb = ram_limit_gb > 0.0 ? ram_limit_gb : 0.0;
    locked.state.vram_limit_gb = vram_limit_gb > 0.0 ? vram_limit_gb : 0.0;
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_admit_model_job(
    const char *specialty,
    const char *model_name,
    int niceness,
    double requested_ram_gb,
    double requested_vram_gb,
    int *allowed,
    char *state,
    size_t state_size,
    char *reason,
    size_t reason_size) {
    if (allowed == NULL || state == NULL || state_size == 0 || reason == NULL || reason_size == 0) {
        return -1;
    }
    bubblehub_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }

    int existing_index = find_model(&locked.state, model_name);
    if (existing_index >= 0) {
        locked.state.models[existing_index].last_used = bubblehub_now_seconds();
        *allowed = 1;
        copy_field(state, state_size, ram_state(&locked.state, 0.0));
        copy_field(reason, reason_size, "");
        return unlock_state(&locked, 1);
    }

    int fits_after_eviction = evict_idle_until_fits(&locked.state, model_name, requested_ram_gb, requested_vram_gb);
    const char *current_state = ram_state(&locked.state, requested_ram_gb);
    const char *current_reason = "";
    int is_allowed = fits_after_eviction;
    if (!fits_after_eviction) {
        current_reason = "not enough RAM/VRAM and no idle model can be evicted";
    } else if (strcmp(current_state, "no_ram") == 0 && niceness > 0) {
        is_allowed = 0;
        current_reason = "no RAM: background job queued until memory is freed";
    } else if (strcmp(current_state, "low") == 0 && niceness >= 10) {
        is_allowed = 0;
        current_reason = "RAM low: background job waiting behind higher-priority work";
    } else {
        double vram_total_gb = effective_vram_limit_gb(&locked.state);
        if (
            requested_vram_gb > 0.0 &&
            vram_total_gb > 0.0 &&
            committed_vram_gb(&locked.state) + requested_vram_gb + (vram_total_gb * BUBBLEHUB_GPU_RESERVED_VRAM_PERCENT / 100.0) > vram_total_gb &&
            niceness > 0) {
            is_allowed = 0;
            current_reason = "VRAM low: background job queued";
        }
    }

    *allowed = is_allowed;
    copy_field(state, state_size, current_state);
    copy_field(reason, reason_size, current_reason);
    BUBBLEHUB_LOG_DEBUG(
        "admitted model job",
        "model=%s allowed=%d state=%s reason=%s",
        model_name,
        is_allowed,
        current_state,
        current_reason);
    if (!is_allowed) {
        char job_id[BUBBLEHUB_FIELD_SMALL];
        snprintf(job_id, sizeof(job_id), "job-%ld-%d", (long)time(NULL), (int)getpid());
        add_queue_item_locked(&locked.state, job_id, "model_load", specialty, model_name, niceness, current_reason);
    }
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_register_agent(
    const char *agent_id,
    int64_t pid,
    const char *binary,
    int niceness,
    const char *specialty) {
    if (agent_id == NULL || agent_id[0] == '\0') {
        return -1;
    }
    bubblehub_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_agent(&locked.state, agent_id);
    if (index < 0) {
        index = find_free_agent(&locked.state);
    }
    if (index < 0) {
        unlock_state(&locked, 0);
        return -1;
    }

    bubblehub_agent_record *record = &locked.state.agents[index];
    memset(record, 0, sizeof(*record));
    record->active = 1;
    copy_field(record->agent_id, sizeof(record->agent_id), agent_id);
    record->pid = pid;
    copy_field(record->binary, sizeof(record->binary), binary);
    record->niceness = niceness;
    copy_field(record->status, sizeof(record->status), "running");
    copy_field(record->specialty, sizeof(record->specialty), specialty);
    record->registered_at = bubblehub_now_seconds();
    BUBBLEHUB_LOG_INFO("registered agent", "agent_id=%s pid=%lld binary=%s", agent_id, (long long)pid, binary);
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_deregister_agent(const char *agent_id) {
    bubblehub_locked_state locked;
    if (agent_id == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_agent(&locked.state, agent_id);
    if (index >= 0) {
        memset(&locked.state.agents[index], 0, sizeof(locked.state.agents[index]));
        BUBBLEHUB_LOG_INFO("deregistered agent", "agent_id=%s", agent_id);
    }
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_mark_model_loaded(
    const char *name,
    const char *specialty,
    const char *backend,
    double ram_gb,
    double vram_gb,
    int64_t pid,
    int port) {
    if (name == NULL || name[0] == '\0') {
        return -1;
    }
    bubblehub_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_model(&locked.state, name);
    if (index >= 0) {
        locked.state.models[index].refcount += 1;
        locked.state.models[index].pid = pid;
        locked.state.models[index].port = port;
        locked.state.models[index].last_used = bubblehub_now_seconds();
        return unlock_state(&locked, 1);
    }
    index = find_free_model(&locked.state);
    if (index < 0) {
        unlock_state(&locked, 0);
        return -1;
    }
    bubblehub_model_record *record = &locked.state.models[index];
    memset(record, 0, sizeof(*record));
    record->active = 1;
    copy_field(record->name, sizeof(record->name), name);
    copy_field(record->specialty, sizeof(record->specialty), specialty);
    copy_field(record->backend, sizeof(record->backend), backend);
    record->ram_gb = ram_gb;
    record->vram_gb = vram_gb;
    record->pid = pid;
    record->port = port;
    record->refcount = 1;
    record->loaded_at = bubblehub_now_seconds();
    record->last_used = record->loaded_at;
    BUBBLEHUB_LOG_INFO(
        "marked model loaded",
        "name=%s backend=%s pid=%lld port=%d",
        name,
        backend,
        (long long)pid,
        port);
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_mark_model_unloaded(const char *name) {
    bubblehub_locked_state locked;
    if (name == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_model(&locked.state, name);
    if (index >= 0) {
        if (locked.state.models[index].refcount > 0) {
            locked.state.models[index].refcount -= 1;
        }
        locked.state.models[index].last_used = bubblehub_now_seconds();
    }
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_evict_model(const char *name) {
    bubblehub_locked_state locked;
    if (name == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_model(&locked.state, name);
    if (index >= 0) {
        evict_model_at(&locked.state, index);
        BUBBLEHUB_LOG_INFO("evicted model", "name=%s", name);
    }
    return unlock_state(&locked, 1);
}

int bubblehub_scheduler_add_queue_item(
    const char *job_id,
    const char *kind,
    const char *specialty,
    const char *model_name,
    int niceness,
    const char *reason) {
    bubblehub_locked_state locked;
    if (job_id == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    add_queue_item_locked(&locked.state, job_id, kind, specialty, model_name, niceness, reason);
    return unlock_state(&locked, 1);
}

static void json_append(bubblehub_json_builder *builder, const char *fmt, ...) {
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

static void json_string(bubblehub_json_builder *builder, const char *value) {
    json_append(builder, "\"");
    if (value != NULL) {
        for (const unsigned char *p = (const unsigned char *)value; *p != '\0'; p++) {
            if (*p == '"' || *p == '\\') {
                json_append(builder, "\\%c", *p);
            } else if (*p >= 0x20) {
                json_append(builder, "%c", *p);
            }
        }
    }
    json_append(builder, "\"");
}

char *bubblehub_scheduler_snapshot_json(void) {
    bubblehub_locked_state locked;
    if (lock_state(&locked) != 0) {
        return NULL;
    }
    char *buffer = malloc(BUBBLEHUB_JSON_CAPACITY);
    if (buffer == NULL) {
        unlock_state(&locked, 0);
        return NULL;
    }
    bubblehub_json_builder json = {
        .data = buffer,
        .len = 0,
        .cap = BUBBLEHUB_JSON_CAPACITY,
        .failed = 0,
    };
    const char *current_memory_pressure = ram_state(&locked.state, 0.0);
    json_append(
        &json,
        "{\"hardware\":{\"ram_bytes\":%llu,\"vram_bytes\":%llu,\"free_vram_bytes\":%llu},\"limits\":{\"ram_bytes\":%llu,\"vram_bytes\":%llu},\"memory_pressure\":",
        (unsigned long long)bubblehub_hw_total_ram_bytes(),
        (unsigned long long)bubblehub_hw_vram_bytes(),
        (unsigned long long)bubblehub_hw_free_vram_bytes(),
        (unsigned long long)(effective_ram_limit_gb(&locked.state) * 1073741824.0),
        (unsigned long long)(effective_vram_limit_gb(&locked.state) * 1073741824.0));
    json_string(&json, current_memory_pressure);
    json_append(&json, ",\"agents\":[");
    int first = 1;
    for (int i = 0; i < BUBBLEHUB_MAX_AGENTS; i++) {
        bubblehub_agent_record *item = &locked.state.agents[i];
        if (!item->active) {
            continue;
        }
        json_append(&json, "%s{\"agent_id\":", first ? "" : ",");
        json_string(&json, item->agent_id);
        json_append(&json, ",\"pid\":%lld,\"binary\":", (long long)item->pid);
        json_string(&json, item->binary);
        json_append(&json, ",\"niceness\":%d,\"status\":", item->niceness);
        json_string(&json, item->status);
        json_append(&json, ",\"specialty\":");
        json_string(&json, item->specialty);
        json_append(&json, ",\"age_seconds\":%.0f}", bubblehub_now_seconds() - item->registered_at);
        first = 0;
    }
    json_append(&json, "],\"models\":[");
    first = 1;
    for (int i = 0; i < BUBBLEHUB_MAX_MODELS; i++) {
        bubblehub_model_record *item = &locked.state.models[i];
        if (!item->active) {
            continue;
        }
        json_append(&json, "%s{\"name\":", first ? "" : ",");
        json_string(&json, item->name);
        json_append(&json, ",\"specialty\":");
        json_string(&json, item->specialty);
        json_append(&json, ",\"backend\":");
        json_string(&json, item->backend);
        json_append(
            &json,
            ",\"ram_gb\":%.3f,\"vram_gb\":%.3f,\"pid\":%lld,\"port\":%d,\"refcount\":%d,\"age_seconds\":%.0f,\"idle_seconds\":%.0f}",
            item->ram_gb,
            item->vram_gb,
            (long long)item->pid,
            item->port,
            item->refcount,
            bubblehub_now_seconds() - item->loaded_at,
            bubblehub_now_seconds() - item->last_used);
        first = 0;
    }
    json_append(&json, "],\"queue\":[");
    first = 1;
    for (int i = 0; i < BUBBLEHUB_MAX_QUEUE; i++) {
        bubblehub_queue_record *item = &locked.state.queue[i];
        if (!item->active) {
            continue;
        }
        json_append(&json, "%s{\"job_id\":", first ? "" : ",");
        json_string(&json, item->job_id);
        json_append(&json, ",\"kind\":");
        json_string(&json, item->kind);
        json_append(&json, ",\"specialty\":");
        json_string(&json, item->specialty);
        json_append(&json, ",\"model_name\":");
        json_string(&json, item->model_name);
        json_append(&json, ",\"niceness\":%d,\"wait_seconds\":%.0f,\"reason\":", item->niceness, bubblehub_now_seconds() - item->created_at);
        json_string(&json, item->reason);
        json_append(&json, "}");
        first = 0;
    }
    json_append(&json, "]}");
    unlock_state(&locked, 0);
    if (json.failed) {
        free(buffer);
        return NULL;
    }
    return buffer;
}

void bubblehub_scheduler_free_string(char *value) {
    free(value);
}

typedef struct {
    char specialty[BUBBLEHUB_FIELD_SMALL];
    char model_name[BUBBLEHUB_FIELD_SMALL];
    char backend[BUBBLEHUB_FIELD_SMALL];
    char model_path[BUBBLEHUB_FIELD_LARGE];
    char messages_json[8192];
    double ram_gb;
    double vram_gb;
    int niceness;
    int max_tokens;
    int gpu_layers;
} bubblehub_chat_request;

static char *json_response_error(const char *message) {
    char *buffer = malloc(BUBBLEHUB_JSON_CAPACITY);
    if (buffer == NULL) {
        return NULL;
    }
    bubblehub_json_builder builder = {
        .data = buffer,
        .len = 0,
        .cap = BUBBLEHUB_JSON_CAPACITY,
        .failed = 0,
    };
    json_append(&builder, "{\"error\":");
    json_string(&builder, message == NULL ? "native inference failed" : message);
    json_append(&builder, "}");
    return builder.data;
}

static int json_get_string_field(const char *json, const char *field, char *buffer, size_t buffer_size) {
    if (json == NULL || field == NULL || buffer == NULL || buffer_size == 0) {
        return -1;
    }
    buffer[0] = '\0';
    char pattern[128];
    snprintf(pattern, sizeof(pattern), "\"%s\"", field);
    const char *cursor = strstr(json, pattern);
    if (cursor == NULL) {
        return -1;
    }
    cursor += strlen(pattern);
    while (*cursor == ' ' || *cursor == '\t' || *cursor == '\n' || *cursor == '\r') {
        cursor++;
    }
    if (*cursor != ':') {
        return -1;
    }
    cursor++;
    while (*cursor == ' ' || *cursor == '\t' || *cursor == '\n' || *cursor == '\r') {
        cursor++;
    }
    if (*cursor != '"') {
        return -1;
    }
    cursor++;
    size_t out = 0;
    while (*cursor != '\0' && *cursor != '"') {
        char ch = *cursor++;
        if (ch == '\\') {
            char escaped = *cursor++;
            if (escaped == '\0') {
                return -1;
            }
            switch (escaped) {
                case 'n':
                    ch = '\n';
                    break;
                case 'r':
                    ch = '\r';
                    break;
                case 't':
                    ch = '\t';
                    break;
                case '\\':
                case '"':
                case '/':
                    ch = escaped;
                    break;
                default:
                    ch = escaped;
                    break;
            }
        }
        if (out + 1 >= buffer_size) {
            return -1;
        }
        buffer[out++] = ch;
    }
    if (*cursor != '"') {
        return -1;
    }
    buffer[out] = '\0';
    return 0;
}

static double json_get_double_field(const char *json, const char *field, double default_value) {
    char pattern[128];
    snprintf(pattern, sizeof(pattern), "\"%s\"", field);
    const char *cursor = strstr(json, pattern);
    if (cursor == NULL) {
        return default_value;
    }
    cursor += strlen(pattern);
    while (*cursor != '\0' && *cursor != ':') {
        cursor++;
    }
    if (*cursor != ':') {
        return default_value;
    }
    cursor++;
    char *end = NULL;
    double value = strtod(cursor, &end);
    return end == cursor ? default_value : value;
}

static int json_get_int_field(const char *json, const char *field, int default_value) {
    double value = json_get_double_field(json, field, (double)default_value);
    return (int)value;
}

static int parse_chat_request(const char *request_json, bubblehub_chat_request *request) {
    if (request_json == NULL || request == NULL) {
        return -1;
    }
    memset(request, 0, sizeof(*request));
    request->max_tokens = 512;
    request->gpu_layers = -999999;
    if (
        json_get_string_field(request_json, "specialty", request->specialty, sizeof(request->specialty)) != 0 ||
        json_get_string_field(request_json, "model_name", request->model_name, sizeof(request->model_name)) != 0 ||
        json_get_string_field(request_json, "backend", request->backend, sizeof(request->backend)) != 0 ||
        json_get_string_field(request_json, "model_path", request->model_path, sizeof(request->model_path)) != 0 ||
        json_get_string_field(request_json, "messages_json", request->messages_json, sizeof(request->messages_json)) != 0) {
        return -1;
    }
    request->ram_gb = json_get_double_field(request_json, "ram_gb", 0.0);
    request->vram_gb = json_get_double_field(request_json, "vram_gb", 0.0);
    request->niceness = json_get_int_field(request_json, "niceness", 0);
    request->max_tokens = json_get_int_field(request_json, "max_tokens", 512);
    request->gpu_layers = json_get_int_field(request_json, "gpu_layers", -999999);
    return 0;
}

static int model_process_record(const char *model_name, int64_t *pid, int *port) {
    bubblehub_locked_state locked;
    if (lock_state(&locked) != 0) {
        return 0;
    }
    int index = find_model(&locked.state, model_name);
    if (index < 0) {
        unlock_state(&locked, 0);
        return 0;
    }
    *pid = locked.state.models[index].pid;
    *port = locked.state.models[index].port;
    unlock_state(&locked, 0);
    return 1;
}

static int pid_is_running(int64_t pid) {
    if (pid <= 0) {
        return 0;
    }
    if (kill((pid_t)pid, 0) == 0) {
        return 1;
    }
    return errno == EPERM;
}

static int connect_localhost(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((uint16_t)port);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static int read_http_response(int fd, char *buffer, size_t buffer_size) {
    size_t used = 0;
    while (used + 1 < buffer_size) {
        ssize_t count = read(fd, buffer + used, buffer_size - used - 1);
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        if (count == 0) {
            break;
        }
        used += (size_t)count;
    }
    buffer[used] = '\0';
    return 0;
}

static int http_get_health(int port) {
    int fd = connect_localhost(port);
    if (fd < 0) {
        return 0;
    }
    const char *request = "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    ssize_t ignored = write(fd, request, strlen(request));
    (void)ignored;
    char response[4096];
    int ok = read_http_response(fd, response, sizeof(response)) == 0 && strstr(response, "HTTP/1.1 5") == NULL && strstr(response, "HTTP/1.0 5") == NULL && strstr(response, "HTTP/") != NULL;
    close(fd);
    return ok;
}

static int allocate_local_port(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return 0;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(fd);
        return 0;
    }
    socklen_t len = sizeof(addr);
    if (getsockname(fd, (struct sockaddr *)&addr, &len) != 0) {
        close(fd);
        return 0;
    }
    int port = ntohs(addr.sin_port);
    close(fd);
    return port;
}

static int wait_for_backend_health(pid_t pid, int port, int timeout_seconds) {
    time_t deadline = time(NULL) + timeout_seconds;
    while (time(NULL) < deadline) {
        int status = 0;
        pid_t exited = waitpid(pid, &status, WNOHANG);
        if (exited == pid) {
            return 0;
        }
        if (exited < 0 && errno != EINTR) {
            return 0;
        }
        if (kill(pid, 0) != 0 && errno != EPERM) {
            return 0;
        }
        if (http_get_health(port)) {
            return 1;
        }
        usleep(250000);
    }
    return 0;
}

static int spawn_llama_backend(const bubblehub_chat_request *request, int *port_out, int64_t *pid_out) {
    int port = allocate_local_port();
    if (port <= 0) {
        return -1;
    }
    char port_text[32];
    snprintf(port_text, sizeof(port_text), "%d", port);
    const char *ctx_size = getenv("BUBBLEHUB_LLAMA_CTX_SIZE");
    if (ctx_size == NULL || ctx_size[0] == '\0') {
        ctx_size = "32768";
    }
    const char *parallel = getenv("BUBBLEHUB_LLAMA_PARALLEL");
    if (parallel == NULL || parallel[0] == '\0') {
        parallel = "1";
    }
    char gpu_layers[32];
    int use_gpu_layers = request->gpu_layers != -999999;
    if (use_gpu_layers) {
        snprintf(gpu_layers, sizeof(gpu_layers), "%d", request->gpu_layers);
    }

    char log_template[] = "/tmp/bubblehub-llama-native-XXXXXX.log";
    int log_fd = mkstemps(log_template, 4);
    if (log_fd < 0) {
        log_fd = open("/dev/null", O_WRONLY);
    }

    pid_t pid = fork();
    if (pid < 0) {
        if (log_fd >= 0) {
            close(log_fd);
        }
        return -1;
    }
    if (pid == 0) {
        if (log_fd >= 0) {
            dup2(log_fd, STDOUT_FILENO);
            dup2(log_fd, STDERR_FILENO);
        }
        if (use_gpu_layers) {
            execlp(
                "llama-server",
                "llama-server",
                "--model",
                request->model_path,
                "--host",
                "127.0.0.1",
                "--port",
                port_text,
                "--ctx-size",
                ctx_size,
                "--parallel",
                parallel,
                "--n-gpu-layers",
                gpu_layers,
                (char *)NULL);
        } else {
            execlp(
                "llama-server",
                "llama-server",
                "--model",
                request->model_path,
                "--host",
                "127.0.0.1",
                "--port",
                port_text,
                "--ctx-size",
                ctx_size,
                "--parallel",
                parallel,
                (char *)NULL);
        }
        _exit(127);
    }
    if (log_fd >= 0) {
        close(log_fd);
    }
    BUBBLEHUB_LOG_DEBUG("spawned llama backend", "port=%d pid=%lld log=%s", port, (long long)pid, log_template);
    if (!wait_for_backend_health(pid, port, 120)) {
        BUBBLEHUB_LOG_ERROR("llama backend failed health check", "port=%d pid=%lld log=%s", port, (long long)pid, log_template);
        kill(pid, SIGTERM);
        return -1;
    }
    *port_out = port;
    *pid_out = (int64_t)pid;
    return 0;
}

static int spawn_vllm_backend(const bubblehub_chat_request *request, int *port_out, int64_t *pid_out) {
    int port = allocate_local_port();
    if (port <= 0) {
        return -1;
    }
    char port_text[32];
    snprintf(port_text, sizeof(port_text), "%d", port);
    char log_template[] = "/tmp/bubblehub-vllm-native-XXXXXX.log";
    int log_fd = mkstemps(log_template, 4);
    if (log_fd < 0) {
        log_fd = open("/dev/null", O_WRONLY);
    }

    pid_t pid = fork();
    if (pid < 0) {
        if (log_fd >= 0) {
            close(log_fd);
        }
        return -1;
    }
    if (pid == 0) {
        if (log_fd >= 0) {
            dup2(log_fd, STDOUT_FILENO);
            dup2(log_fd, STDERR_FILENO);
        }
        const char *cuda_devices = getenv("BUBBLEHUB_CUDA_VISIBLE_DEVICES");
        if (cuda_devices != NULL && cuda_devices[0] != '\0') {
            setenv("CUDA_VISIBLE_DEVICES", cuda_devices, 1);
        }
        const char *python = getenv("BUBBLEHUB_PYTHON");
        if (python == NULL || python[0] == '\0') {
            python = "python3";
        }
        execlp(
            python,
            python,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            request->model_path,
            "--served-model-name",
            request->model_name,
            "--host",
            "127.0.0.1",
            "--port",
            port_text,
            (char *)NULL);
        _exit(127);
    }
    if (log_fd >= 0) {
        close(log_fd);
    }
    BUBBLEHUB_LOG_DEBUG("spawned vllm backend", "port=%d pid=%lld log=%s", port, (long long)pid, log_template);
    if (!wait_for_backend_health(pid, port, 180)) {
        BUBBLEHUB_LOG_ERROR("vllm backend failed health check", "port=%d pid=%lld log=%s", port, (long long)pid, log_template);
        kill(pid, SIGTERM);
        return -1;
    }
    *port_out = port;
    *pid_out = (int64_t)pid;
    return 0;
}

static int ensure_native_model_loaded(const bubblehub_chat_request *request, int *port_out, int64_t *pid_out) {
    int allowed = 0;
    char state[BUBBLEHUB_FIELD_SMALL];
    char reason[BUBBLEHUB_FIELD_MEDIUM];
    if (
        bubblehub_scheduler_admit_model_job(
            request->specialty,
            request->model_name,
            request->niceness,
            request->ram_gb,
            request->vram_gb,
            &allowed,
            state,
            sizeof(state),
            reason,
            sizeof(reason)) != 0 ||
        !allowed) {
        return -1;
    }

    int port = 0;
    int64_t pid = 0;
    if (model_process_record(request->model_name, &pid, &port)) {
        if (port > 0 && pid_is_running(pid) && http_get_health(port)) {
            BUBBLEHUB_LOG_INFO("reusing loaded model", "model=%s port=%d pid=%lld", request->model_name, port, (long long)pid);
            bubblehub_scheduler_mark_model_loaded(
                request->model_name,
                request->specialty,
                request->backend,
                request->ram_gb,
                request->vram_gb,
                pid,
                port);
            *port_out = port;
            *pid_out = pid;
            return 0;
        }
        bubblehub_scheduler_evict_model(request->model_name);
    }

    int started = -1;
    if (strcmp(request->backend, "llama") == 0) {
        started = spawn_llama_backend(request, &port, &pid);
    } else if (strcmp(request->backend, "vllm") == 0) {
        started = spawn_vllm_backend(request, &port, &pid);
    }
    if (started != 0) {
        return -1;
    }
    bubblehub_scheduler_mark_model_loaded(
        request->model_name,
        request->specialty,
        request->backend,
        request->ram_gb,
        request->vram_gb,
        pid,
        port);
    *port_out = port;
    *pid_out = pid;
    return 0;
}

static int http_post_chat(int port, const bubblehub_chat_request *request, char *response, size_t response_size) {
    int fd = connect_localhost(port);
    if (fd < 0) {
        return -1;
    }
    char payload[12288];
    int written = snprintf(
        payload,
        sizeof(payload),
        "{\"model\":\"%s\",\"messages\":%s,\"stream\":false,\"max_tokens\":%d}",
        request->model_name,
        request->messages_json,
        request->max_tokens);
    if (written < 0 || (size_t)written >= sizeof(payload)) {
        close(fd);
        return -1;
    }
    char header[512];
    written = snprintf(
        header,
        sizeof(header),
        "POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n",
        strlen(payload));
    if (written < 0 || (size_t)written >= sizeof(header)) {
        close(fd);
        return -1;
    }
    ssize_t ignored = write(fd, header, strlen(header));
    ignored = write(fd, payload, strlen(payload));
    (void)ignored;
    int rc = read_http_response(fd, response, response_size);
    close(fd);
    return rc;
}

static int extract_chat_content(const char *http_response, char *content, size_t content_size) {
    const char *body = strstr(http_response, "\r\n\r\n");
    if (body == NULL) {
        return -1;
    }
    body += 4;
    return json_get_string_field(body, "content", content, content_size);
}

static int parse_local_http_base(const char *base_url, int *port_out) {
    if (base_url == NULL || port_out == NULL) {
        return -1;
    }
    const char *cursor = base_url;
    const char *prefix = "http://";
    if (strncmp(cursor, prefix, strlen(prefix)) == 0) {
        cursor += strlen(prefix);
    }
    if (strncmp(cursor, "127.0.0.1", 9) != 0 && strncmp(cursor, "localhost", 9) != 0) {
        return -1;
    }
    const char *colon = strchr(cursor, ':');
    if (colon == NULL) {
        *port_out = 80;
        return 0;
    }
    *port_out = atoi(colon + 1);
    return *port_out > 0 ? 0 : -1;
}

static int native_should_forward_to_sandbox_endpoint(void) {
    const char *network = getenv("BUBBLEHUB_NETWORK");
    const char *api_base = getenv("BUBBLEHUB_API_BASE_URL");
    return network != NULL && strcmp(network, "inference-only") == 0 && api_base != NULL && api_base[0] != '\0';
}

static int http_post_sandbox_inference(const bubblehub_chat_request *request, char *response, size_t response_size) {
    int port = 0;
    if (parse_local_http_base(getenv("BUBBLEHUB_API_BASE_URL"), &port) != 0) {
        return -1;
    }
    int fd = connect_localhost(port);
    if (fd < 0) {
        return -1;
    }

    char *buffer = malloc(BUBBLEHUB_JSON_CAPACITY);
    if (buffer == NULL) {
        close(fd);
        return -1;
    }
    bubblehub_json_builder payload_builder = {
        .data = buffer,
        .len = 0,
        .cap = BUBBLEHUB_JSON_CAPACITY,
        .failed = 0,
    };
    json_append(&payload_builder, "{\"model\":");
    json_string(&payload_builder, request->specialty);
    json_append(&payload_builder, ",\"bubblehub_specialty\":");
    json_string(&payload_builder, request->specialty);
    json_append(&payload_builder, ",\"messages\":%s,\"max_tokens\":%d}", request->messages_json, request->max_tokens);
    if (payload_builder.failed) {
        free(buffer);
        close(fd);
        return -1;
    }

    char header[512];
    int written = snprintf(
        header,
        sizeof(header),
        "POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n",
        strlen(payload_builder.data));
    if (written < 0 || (size_t)written >= sizeof(header)) {
        free(buffer);
        close(fd);
        return -1;
    }
    ssize_t ignored = write(fd, header, strlen(header));
    ignored = write(fd, payload_builder.data, strlen(payload_builder.data));
    (void)ignored;
    free(buffer);
    int rc = read_http_response(fd, response, response_size);
    close(fd);
    return rc;
}

char *bubblehub_inference_chat_json(const char *request_json) {
    bubblehub_chat_request request;
    if (parse_chat_request(request_json, &request) != 0) {
        return json_response_error("invalid native inference request");
    }

    if (native_should_forward_to_sandbox_endpoint()) {
        char http_response[65536];
        char content[32768];
        if (http_post_sandbox_inference(&request, http_response, sizeof(http_response)) != 0 || extract_chat_content(http_response, content, sizeof(content)) != 0) {
            return json_response_error("native sandbox inference forward failed");
        }
        char *buffer = malloc(BUBBLEHUB_JSON_CAPACITY);
        if (buffer == NULL) {
            return NULL;
        }
        bubblehub_json_builder builder = {
            .data = buffer,
            .len = 0,
            .cap = BUBBLEHUB_JSON_CAPACITY,
            .failed = 0,
        };
        json_append(&builder, "{\"content\":");
        json_string(&builder, content);
        json_append(&builder, ",\"model\":");
        json_string(&builder, request.model_name);
        json_append(&builder, ",\"pid\":0,\"port\":0}");
        return builder.data;
    }

    int port = 0;
    int64_t pid = 0;
    if (ensure_native_model_loaded(&request, &port, &pid) != 0) {
        return json_response_error("failed to load or attach model in libbubble");
    }

    char http_response[65536];
    char content[32768];
    int rc = http_post_chat(port, &request, http_response, sizeof(http_response));
    bubblehub_scheduler_mark_model_unloaded(request.model_name);
    if (rc != 0 || extract_chat_content(http_response, content, sizeof(content)) != 0) {
        return json_response_error("native model chat request failed");
    }

    char *buffer = malloc(BUBBLEHUB_JSON_CAPACITY);
    if (buffer == NULL) {
        return NULL;
    }
    bubblehub_json_builder builder = {
        .data = buffer,
        .len = 0,
        .cap = BUBBLEHUB_JSON_CAPACITY,
        .failed = 0,
    };
    json_append(&builder, "{\"content\":");
    json_string(&builder, content);
    json_append(&builder, ",\"model\":");
    json_string(&builder, request.model_name);
    json_append(&builder, ",\"pid\":%lld,\"port\":%d}", (long long)pid, port);
    return builder.data;
}
