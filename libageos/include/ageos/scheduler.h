#pragma once

#include <stddef.h>
#include <stdint.h>

int ageos_scheduler_admit_model_job(
    const char *specialty,
    const char *model_name,
    int niceness,
    double requested_ram_gb,
    double requested_vram_gb,
    int *allowed,
    char *state,
    size_t state_size,
    char *reason,
    size_t reason_size
);
int ageos_scheduler_configure_limits(double ram_limit_gb, double vram_limit_gb);
int ageos_scheduler_register_agent(
    const char *agent_id,
    int64_t pid,
    const char *binary,
    int niceness,
    const char *specialty
);
int ageos_scheduler_deregister_agent(const char *agent_id);
int ageos_scheduler_mark_model_loaded(
    const char *name,
    const char *specialty,
    const char *backend,
    double ram_gb,
    double vram_gb,
    int64_t pid,
    int port
);
int ageos_scheduler_mark_model_unloaded(const char *name);
int ageos_scheduler_evict_model(const char *name);
int ageos_scheduler_add_queue_item(
    const char *job_id,
    const char *kind,
    const char *specialty,
    const char *model_name,
    int niceness,
    const char *reason
);
char *ageos_scheduler_snapshot_json(void);
void ageos_scheduler_free_string(char *value);
char *ageos_inference_chat_json(const char *request_json);
