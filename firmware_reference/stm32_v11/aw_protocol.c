#include "aw_protocol.h"

#include <string.h>

#define AW_SYNC_FIRST 0xA5u
#define AW_SYNC_SECOND 0x5Au
#define AW_PAYLOAD_OFFSET 7u

static uint16_t read_u16(const uint8_t *bytes)
{
    return (uint16_t)((uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8));
}

static uint32_t read_u32(const uint8_t *bytes)
{
    return (uint32_t)read_u16(bytes) | ((uint32_t)read_u16(bytes + 2) << 16);
}

static uint64_t read_u64(const uint8_t *bytes)
{
    return (uint64_t)read_u32(bytes) | ((uint64_t)read_u32(bytes + 4) << 32);
}

static int32_t read_i32(const uint8_t *bytes)
{
    uint32_t raw = read_u32(bytes);
    /* Avoid implementation-defined conversion of uint32_t above INT32_MAX. */
    return raw <= INT32_MAX ? (int32_t)raw : -1 - (int32_t)(UINT32_MAX - raw);
}

static void write_u16(uint8_t *bytes, uint16_t value)
{
    bytes[0] = (uint8_t)value;
    bytes[1] = (uint8_t)(value >> 8);
}

static void write_u32(uint8_t *bytes, uint32_t value)
{
    write_u16(bytes, (uint16_t)value);
    write_u16(bytes + 2, (uint16_t)(value >> 16));
}

static void write_u64(uint8_t *bytes, uint64_t value)
{
    write_u32(bytes, (uint32_t)value);
    write_u32(bytes + 4, (uint32_t)(value >> 32));
}

uint16_t aw_crc16(const uint8_t *bytes, size_t size)
{
    uint16_t crc = 0xFFFFu;
    size_t i;

    for (i = 0; i < size; ++i) {
        unsigned bit;
        crc ^= (uint16_t)((uint16_t)bytes[i] << 8);
        for (bit = 0; bit < 8; ++bit) {
            uint16_t polynomial = (crc & 0x8000u) != 0 ? 0x1021u : 0u;
            crc = (uint16_t)(((uint32_t)crc << 1) ^ polynomial);
        }
    }
    return crc;
}

bool aw_seq_is_new(uint16_t next, uint16_t last)
{
    uint16_t delta = (uint16_t)(next - last);
    return delta != 0 && delta < 0x8000u;
}

uint32_t aw_elapsed_ms(uint32_t now, uint32_t then)
{
    return now - then;
}

bool aw_sample_time_is_new(uint32_t next, uint32_t last)
{
    uint32_t delta = aw_elapsed_ms(next, last);
    return delta != 0 && delta < UINT32_C(0x80000000);
}

static uint8_t payload_size(uint8_t type)
{
    switch (type) {
    case AW_TELEMETRY: return 30;
    case AW_BRAKE_COMMAND: return 13;
    case AW_SESSION_OPEN:
    case AW_SESSION_ACK: return 8;
    default: return 0;
    }
}

static bool command_valid(const AwCommand *command)
{
    return command->session_id != 0
        && command->valid_for_ms >= 20 && command->valid_for_ms <= 500
        && (command->mode == AW_ENABLE
            || (command->mode == AW_STOP && command->target_torque_mnm == 0));
}

static void write_telemetry(uint8_t *bytes, const AwTelemetry *telemetry)
{
    write_u32(bytes, (uint32_t)telemetry->angle_mrad);
    write_u32(bytes + 4, (uint32_t)telemetry->velocity_mrad_s);
    write_u32(bytes + 8, (uint32_t)telemetry->sensor_force_mn);
    write_u16(bytes + 12, telemetry->status_flags);
    write_u16(bytes + 14, telemetry->last_command_seq);
    write_u16(bytes + 16, telemetry->applied_target_mnm);
    write_u32(bytes + 18, telemetry->mcu_sample_ms);
    write_u64(bytes + 22, telemetry->session_id);
}

static void read_telemetry(const uint8_t *bytes, AwTelemetry *telemetry)
{
    telemetry->angle_mrad = read_i32(bytes);
    telemetry->velocity_mrad_s = read_i32(bytes + 4);
    telemetry->sensor_force_mn = read_i32(bytes + 8);
    telemetry->status_flags = read_u16(bytes + 12);
    telemetry->last_command_seq = read_u16(bytes + 14);
    telemetry->applied_target_mnm = read_u16(bytes + 16);
    telemetry->mcu_sample_ms = read_u32(bytes + 18);
    telemetry->session_id = read_u64(bytes + 22);
}

size_t aw_encode(const AwFrame *frame, uint8_t *out, size_t capacity)
{
    uint8_t length = payload_size(frame->type);
    size_t size = AW_FRAME_OVERHEAD + length;
    uint8_t *bytes;

    if (length == 0 || capacity < size) {
        return 0;
    }
    bytes = out + AW_PAYLOAD_OFFSET;
    if (frame->type == AW_TELEMETRY) {
        const AwTelemetry *telemetry = &frame->payload.telemetry;
        if ((telemetry->status_flags & ~AW_KNOWN_FLAGS) != 0
            || (telemetry->session_id == 0 && ((telemetry->status_flags & AW_ACK_VALID) != 0
                                              || telemetry->applied_target_mnm != 0))) {
            return 0;
        }
        write_telemetry(bytes, telemetry);
    } else if (frame->type == AW_BRAKE_COMMAND) {
        const AwCommand *command = &frame->payload.command;
        if (!command_valid(command)) {
            return 0;
        }
        write_u64(bytes, command->session_id);
        bytes[8] = command->mode;
        write_u16(bytes + 9, command->target_torque_mnm);
        write_u16(bytes + 11, command->valid_for_ms);
    } else {
        if (frame->payload.session_id == 0) {
            return 0;
        }
        write_u64(bytes, frame->payload.session_id);
    }
    out[0] = AW_SYNC_FIRST;
    out[1] = AW_SYNC_SECOND;
    out[2] = AW_VERSION;
    out[3] = frame->type;
    write_u16(out + 4, frame->seq);
    out[6] = length;
    write_u16(out + size - 2, aw_crc16(out + 2, size - 4));
    return size;
}

static bool header_valid(const uint8_t *bytes)
{
    uint8_t length = payload_size(bytes[3]);
    return bytes[2] == AW_VERSION && length != 0 && bytes[6] == length;
}

static bool crc_valid(const uint8_t *bytes, size_t size)
{
    return aw_crc16(bytes + 2, size - 4) == read_u16(bytes + size - 2);
}

static bool decode_payload(const uint8_t *bytes, AwFrame *out)
{
    AwFrame frame = {0};
    const uint8_t *payload = bytes + AW_PAYLOAD_OFFSET;

    frame.type = bytes[3];
    frame.seq = read_u16(bytes + 4);
    if (frame.type == AW_TELEMETRY) {
        read_telemetry(payload, &frame.payload.telemetry);
    } else if (frame.type == AW_BRAKE_COMMAND) {
        AwCommand *command = &frame.payload.command;
        command->session_id = read_u64(payload);
        command->mode = payload[8];
        command->target_torque_mnm = read_u16(payload + 9);
        command->valid_for_ms = read_u16(payload + 11);
        if (!command_valid(command)) {
            return false;
        }
    } else {
        frame.payload.session_id = read_u64(payload);
        if (frame.payload.session_id == 0) {
            return false;
        }
    }
    *out = frame;
    return true;
}

bool aw_decode(const uint8_t *bytes, size_t size, AwFrame *out)
{
    return size >= AW_FRAME_OVERHEAD && bytes[0] == AW_SYNC_FIRST
        && bytes[1] == AW_SYNC_SECOND && header_valid(bytes)
        && size == AW_FRAME_OVERHEAD + bytes[6] && crc_valid(bytes, size)
        && decode_payload(bytes, out);
}

void aw_parser_init(AwParser *parser)
{
    *parser = (AwParser){0};
}

static void discard(AwParser *parser, size_t count)
{
    parser->used -= count;
    if (parser->used > 0) {
        memmove(parser->bytes, parser->bytes + count, parser->used);
    }
}

static void reset_overrun(AwParser *parser)
{
    ++parser->buffer_overruns;
    parser->used = 0;
    parser->started_ms = 0;
}

bool aw_parser_next(AwParser *parser, uint32_t now_ms, AwFrame *out)
{
    if (parser->used > sizeof(parser->bytes)) {
        reset_overrun(parser);
        return false;
    }
    while (parser->used > 0) {
        size_t offset = 0;
        size_t size;

        /* Preserve a trailing A5 so SYNC may straddle RX chunks. */
        while (offset < parser->used
               && (parser->bytes[offset] != AW_SYNC_FIRST
                   || (offset + 1 < parser->used && parser->bytes[offset + 1] != AW_SYNC_SECOND))) {
            ++offset;
        }
        discard(parser, offset);
        if (parser->used == 0) {
            return false;
        }
        if (parser->used >= AW_PAYLOAD_OFFSET && !header_valid(parser->bytes)) {
            ++parser->rejected_headers;
            discard(parser, 1);
            continue;
        }
        size = parser->used < AW_PAYLOAD_OFFSET
            ? AW_PAYLOAD_OFFSET + 1 : AW_FRAME_OVERHEAD + parser->bytes[6];
        if (parser->used < size) {
            if (aw_elapsed_ms(now_ms, parser->started_ms) < AW_ASSEMBLY_TIMEOUT_MS) {
                return false;
            }
            ++parser->assembly_timeouts;
            discard(parser, 1);
            continue;
        }
        if (!crc_valid(parser->bytes, size)) {
            ++parser->rejected_crc;
            discard(parser, 1);
            continue;
        }
        if (decode_payload(parser->bytes, out)) {
            discard(parser, size);
            parser->started_ms = now_ms;
            return true;
        }
        ++parser->rejected_payloads;
        discard(parser, size);
        parser->started_ms = now_ms;
    }
    return false;
}

bool aw_parser_feed(AwParser *parser, uint8_t byte, uint32_t now_ms, AwFrame *out)
{
    bool available = false;

    /* Discard an abnormal full candidate before any parsing or append. */
    if (parser->used >= sizeof(parser->bytes)) {
        reset_overrun(parser);
    }

    /* Expire an old partial candidate before appending a late byte. */
    if (parser->used > 0 && aw_elapsed_ms(now_ms, parser->started_ms) >= AW_ASSEMBLY_TIMEOUT_MS) {
        available = aw_parser_next(parser, now_ms, out);
    }
    if (parser->used == 0) {
        parser->started_ms = now_ms;
    }
    parser->bytes[parser->used++] = byte;
    return available || aw_parser_next(parser, now_ms, out);
}
