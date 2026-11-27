#ifndef AW_PROTOCOL_H
#define AW_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AW_VERSION 0x02u
#define AW_MAX_PAYLOAD 64u
#define AW_FRAME_OVERHEAD 9u
#define AW_MAX_FRAME (AW_FRAME_OVERHEAD + AW_MAX_PAYLOAD)
#define AW_ASSEMBLY_TIMEOUT_MS 100u

enum AwFrameType {
    AW_TELEMETRY = 1,
    AW_BRAKE_COMMAND = 2,
    AW_SESSION_OPEN = 3,
    AW_SESSION_ACK = 4
};

enum AwMode {
    AW_STOP = 0,
    AW_ENABLE = 1
};

enum AwStatusFlag {
    AW_READY = 1u << 0,
    AW_FAULT = 1u << 1,
    AW_ESTOP = 1u << 2,
    AW_LIMIT = 1u << 3,
    AW_ACK_VALID = 1u << 4,
    AW_FORCE_VALID = 1u << 5,
    AW_ARM_READY = 1u << 6,
    AW_ANGLE_VALID = 1u << 7,
    AW_VEL_VALID = 1u << 8,
    AW_CMD_TIMEOUT = 1u << 9,
    AW_KNOWN_FLAGS = 0x03FFu
};

typedef struct {
    int32_t angle_mrad;
    int32_t velocity_mrad_s;
    int32_t sensor_force_mn;
    uint16_t status_flags;
    uint16_t last_command_seq;
    uint16_t applied_target_mnm;
    uint32_t mcu_sample_ms;
    uint64_t session_id;
} AwTelemetry;

typedef struct {
    uint64_t session_id;
    uint8_t mode;
    uint16_t target_torque_mnm;
    uint16_t valid_for_ms;
} AwCommand;

typedef struct {
    uint8_t type;
    uint16_t seq;
    union {
        AwTelemetry telemetry;
        AwCommand command;
        uint64_t session_id;
    } payload;
} AwFrame;

typedef struct {
    uint8_t bytes[AW_MAX_FRAME];
    size_t used;
    uint32_t started_ms;
    uint32_t rejected_headers;
    uint32_t rejected_crc;
    uint32_t rejected_payloads;
    uint32_t assembly_timeouts;
    uint32_t buffer_overruns; /* Number of capacity resets. */
} AwParser;

uint16_t aw_crc16(const uint8_t *bytes, size_t size);
bool aw_seq_is_new(uint16_t next, uint16_t last);
bool aw_sample_time_is_new(uint32_t next, uint32_t last);
uint32_t aw_elapsed_ms(uint32_t now, uint32_t then);

/* One complete frame. Returns size or zero on invalid fields / short output. */
size_t aw_encode(const AwFrame *frame, uint8_t *out, size_t capacity);
bool aw_decode(const uint8_t *bytes, size_t size, AwFrame *out);

void aw_parser_init(AwParser *parser);
/* Feed each arrived byte. If this returns true, consume out, then drain next. */
bool aw_parser_feed(AwParser *parser, uint8_t byte, uint32_t now_ms, AwFrame *out);
/* Drain after feed, and call periodically even when the line is idle. */
bool aw_parser_next(AwParser *parser, uint32_t now_ms, AwFrame *out);

#endif
