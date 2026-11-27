#include "aw_session.h"

static bool device_permits(uint16_t flags)
{
    const uint16_t required = AW_READY | AW_ANGLE_VALID;
    const uint16_t blocked = AW_FAULT | AW_ESTOP | AW_LIMIT | AW_CMD_TIMEOUT;
    return (flags & required) == required && (flags & blocked) == 0
        && (flags & ~AW_KNOWN_FLAGS) == 0;
}

void aw_session_init(AwSession *session)
{
    *session = (AwSession){0};
}

void aw_session_revoke(AwSession *session)
{
    session->arm_ready = false;
    session->applied_target_mnm = 0;
}

void aw_session_tick(AwSession *session, uint32_t now_ms, uint16_t device_flags)
{
    if (session->command_seen
        && aw_elapsed_ms(now_ms, session->last_command_ms) >= session->lifetime_ms) {
        uint64_t expired = session->id;
        aw_session_init(session);
        session->expired_id = expired;
        session->cmd_timeout = true;
    }
    if (!device_permits(device_flags)) {
        aw_session_revoke(session);
    }
}

bool aw_session_authorize(AwSession *session, uint32_t now_ms, uint16_t device_flags)
{
    aw_session_tick(session, now_ms, device_flags);
    if (session->id == 0 || !session->stop_confirmed || session->cmd_timeout
        || !device_permits(device_flags)) {
        return false;
    }
    session->arm_ready = true;
    return true;
}

AwSessionResult aw_session_handle(AwSession *session, const AwFrame *frame,
                                 uint32_t now_ms, uint16_t device_flags,
                                 uint16_t local_cap_mnm, uint16_t ack_seq, AwFrame *reply)
{
    const AwCommand *command;
    aw_session_tick(session, now_ms, device_flags);
    if (frame->type == AW_SESSION_OPEN) {
        uint64_t id = frame->payload.session_id;
        if (id == 0 || id == session->expired_id) {
            return AW_IGNORE;
        }
        if (id != session->id) {
            bool timed_out = session->cmd_timeout;
            aw_session_init(session);
            session->id = id;
            session->cmd_timeout = timed_out;
        }
        *reply = (AwFrame){ .type = AW_SESSION_ACK, .seq = ack_seq, .payload.session_id = id };
        return AW_SEND_ACK;
    }

    if (frame->type != AW_BRAKE_COMMAND || session->id == 0) {
        return AW_IGNORE;
    }
    command = &frame->payload.command;
    if (command->session_id != session->id
        || (session->command_seen && !aw_seq_is_new(frame->seq, session->last_command_seq))) {
        return AW_IGNORE;
    }
    if (command->mode == AW_STOP) {
        session->applied_target_mnm = 0;
        session->stop_confirmed = true;
        session->cmd_timeout = false;
    } else if (command->mode == AW_ENABLE && session->stop_confirmed && session->arm_ready
               && !session->cmd_timeout && device_permits(device_flags)) {
        session->applied_target_mnm = command->target_torque_mnm < local_cap_mnm
            ? command->target_torque_mnm : local_cap_mnm;
    } else {
        return AW_IGNORE;
    }
    session->last_command_ms = now_ms;
    session->last_command_seq = frame->seq;
    session->lifetime_ms = command->valid_for_ms < AW_LOCAL_MAX_VALID_MS
        ? command->valid_for_ms : AW_LOCAL_MAX_VALID_MS;
    session->command_seen = true;
    return AW_ACCEPT_COMMAND;
}

void aw_session_set_telemetry(const AwSession *session, AwTelemetry *telemetry)
{
    uint16_t flags = telemetry->status_flags;
    flags &= (uint16_t)~(AW_ACK_VALID | AW_ARM_READY | AW_CMD_TIMEOUT);
    if (session->command_seen) {
        flags |= AW_ACK_VALID;
    }
    if (session->arm_ready) {
        flags |= AW_ARM_READY;
    }
    if (session->cmd_timeout) {
        flags |= AW_CMD_TIMEOUT;
    }
    telemetry->status_flags = flags;
    telemetry->last_command_seq = session->command_seen ? session->last_command_seq : 0;
    telemetry->applied_target_mnm = session->applied_target_mnm;
    telemetry->session_id = session->id;
}
