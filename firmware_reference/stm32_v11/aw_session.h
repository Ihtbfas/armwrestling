#ifndef AW_SESSION_H
#define AW_SESSION_H

#include "aw_protocol.h"

#define AW_LOCAL_MAX_VALID_MS 200u

/* Optional board-side reference. The application owns physical protection
 * and all actuator outputs. Call the pure state machine from one context. */
typedef struct {
    uint64_t id;
    uint64_t expired_id;
    uint32_t last_command_ms;
    uint16_t last_command_seq;
    uint16_t applied_target_mnm;
    uint16_t lifetime_ms;
    bool command_seen;
    bool stop_confirmed;
    bool arm_ready;
    bool cmd_timeout;
} AwSession;

typedef enum {
    AW_IGNORE,
    AW_SEND_ACK,
    AW_ACCEPT_COMMAND
} AwSessionResult;

void aw_session_init(AwSession *session);
void aw_session_revoke(AwSession *session);
/* Call periodically, independently of serial reception. */
void aw_session_tick(AwSession *session, uint32_t now_ms, uint16_t device_flags);
/* Call only for a newly granted local permit. Repeated calls from an old
 * latched signal would defeat the ref's revocation after a fault or reset. */
bool aw_session_authorize(AwSession *session, uint32_t now_ms, uint16_t device_flags);
/* A decoded frame from aw_parser_feed/next. On AW_SEND_ACK, send reply.
 * ack_seq is an independent diagnostic counter from the application. */
AwSessionResult aw_session_handle(AwSession *session, const AwFrame *frame,
                                 uint32_t now_ms, uint16_t device_flags,
                                 uint16_t local_cap_mnm, uint16_t ack_seq, AwFrame *reply);
/* Fill transport-owned fields in an existing physical sample snapshot. */
void aw_session_set_telemetry(const AwSession *session, AwTelemetry *telemetry);

#endif
