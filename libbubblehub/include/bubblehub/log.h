#ifndef BUBBLEHUB_LOG_H
#define BUBBLEHUB_LOG_H

typedef enum {
    BUBBLEHUB_LOG_LEVEL_ERROR = 0,
    BUBBLEHUB_LOG_LEVEL_INFO = 1,
    BUBBLEHUB_LOG_LEVEL_DEBUG = 2,
} bubblehub_log_level_t;

void bubblehub_log_init(void);
void bubblehub_log_set_level(const char *level);
void bubblehub_log_set_file(const char *path);
void bubblehub_log_write(int level, const char *file, int line, const char *text, const char *fmt, ...);

#define BUBBLEHUB_LOG_ERROR(text, fmt, ...) \
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_ERROR, __FILE__, __LINE__, text, fmt, ##__VA_ARGS__)
#define BUBBLEHUB_LOG_INFO(text, fmt, ...) \
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_INFO, __FILE__, __LINE__, text, fmt, ##__VA_ARGS__)
#define BUBBLEHUB_LOG_DEBUG(text, fmt, ...) \
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_DEBUG, __FILE__, __LINE__, text, fmt, ##__VA_ARGS__)

#endif
