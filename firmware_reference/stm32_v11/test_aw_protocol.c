#include "aw_protocol.h"
#include "aw_session.h"

/* Test assertions stay active in optimized host builds. */
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SESSION UINT64_C(0x0102030405060708)
#define GOOD_FLAGS (AW_READY | AW_ANGLE_VALID)
#define COUNT(array) (sizeof(array) / sizeof((array)[0]))

typedef struct {
    const char *name;
    uint8_t bytes[AW_MAX_FRAME];
    size_t size;
} Vector;

static Vector vectors[] = {
    {"OPEN", {0}, 0}, {"ACK", {0}, 0}, {"STOP", {0}, 0},
    {"ENABLE", {0}, 0}, {"TELEMETRY", {0}, 0}, {"UNPAIRED", {0}, 0}
};

static void load_vectors(const char *path)
{
    char line[256];
    FILE *file = fopen(path, "r");
    size_t i;
    if (file == NULL) {
        perror(path);
        exit(EXIT_FAILURE);
    }
    for (i = 0; i < COUNT(vectors); ++i) {
        Vector *vector = &vectors[i];
        char *token;
        assert(fgets(line, sizeof(line), file) != NULL);
        token = strtok(line, " \t\r\n");
        assert(token != NULL && strcmp(token, vector->name) == 0);
        while ((token = strtok(NULL, " \t\r\n")) != NULL) {
            char *end;
            unsigned long number = strtoul(token, &end, 16);
            assert(strlen(token) == 2 && *end == '\0' && number <= UINT8_MAX);
            assert(vector->size < sizeof(vector->bytes));
            vector->bytes[vector->size++] = (uint8_t)number;
        }
    }
    assert(fgets(line, sizeof(line), file) == NULL);
    fclose(file);
}

static void test_golden_vectors(void)
{
    uint8_t out[AW_MAX_FRAME];
    size_t i;
    for (i = 0; i < COUNT(vectors); ++i) {
        Vector *vector = &vectors[i];
        AwFrame frame;
        assert(aw_decode(vector->bytes, vector->size, &frame));
        assert(aw_encode(&frame, out, sizeof(out)) == vector->size);
        assert(memcmp(out, vector->bytes, vector->size) == 0);
        assert(aw_encode(&frame, out, vector->size - 1) == 0);
        if (i < 2) {
            assert(frame.type == (i == 0 ? AW_SESSION_OPEN : AW_SESSION_ACK));
            assert(frame.seq == 0 && frame.payload.session_id == SESSION);
        } else if (i < 4) {
            const AwCommand *command = &frame.payload.command;
            assert(frame.type == AW_BRAKE_COMMAND);
            assert(frame.seq == (i == 2 ? 41 : 42));
            assert(command->session_id == SESSION && command->valid_for_ms == 200);
            assert(command->mode == (i == 2 ? AW_STOP : AW_ENABLE));
            assert(command->target_torque_mnm == (i == 2 ? 0 : 600));
        } else {
            const AwTelemetry *sample = &frame.payload.telemetry;
            bool paired = i == 4;
            assert(frame.type == AW_TELEMETRY && frame.seq == (paired ? 34 : 0));
            assert(sample->angle_mrad == -400 && sample->velocity_mrad_s == (paired ? -200 : 0));
            assert(sample->sensor_force_mn == (paired ? 12500 : 0));
            assert(sample->status_flags == (paired ? 0x01F1u : 0x0081u));
            assert(sample->last_command_seq == (paired ? 42 : 0));
            assert(sample->applied_target_mnm == (paired ? 550 : 0));
            assert(sample->mcu_sample_ms == (paired ? 123456u : 12345u));
            assert(sample->session_id == (paired ? SESSION : 0));
        }
    }
}

static void recalc_crc(uint8_t *bytes, size_t size)
{
    uint16_t crc = aw_crc16(bytes + 2, size - 4);
    bytes[size - 2] = (uint8_t)crc;
    bytes[size - 1] = (uint8_t)(crc >> 8);
}

static void test_strict_format(void)
{
    AwFrame frame;
    Vector mutated = vectors[0];
    assert(aw_crc16((const uint8_t *)"123456789", 9) == 0x29B1u);
    mutated.bytes[2] = 1;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated = vectors[0];
    mutated.bytes[3] = 5;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated = vectors[0];
    mutated.bytes[6] = 64;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated = vectors[0];
    mutated.bytes[8] ^= 1;
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));

    mutated = vectors[0];
    memset(mutated.bytes + 7, 0, 8);
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated.bytes[3] = AW_SESSION_ACK;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated = vectors[2];
    memset(mutated.bytes + 7, 0, 8);
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));

    mutated = vectors[2];
    mutated.bytes[16] = 1;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated = vectors[3];
    mutated.bytes[18] = 19;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));
    mutated.bytes[18] = 0xF5;
    mutated.bytes[19] = 0x01;
    recalc_crc(mutated.bytes, mutated.size);
    assert(!aw_decode(mutated.bytes, mutated.size, &frame));

    assert(aw_decode(vectors[4].bytes, vectors[4].size, &frame));
    frame.payload.telemetry.angle_mrad = INT32_MIN;
    frame.payload.telemetry.velocity_mrad_s = INT32_MAX;
    assert(aw_encode(&frame, mutated.bytes, sizeof(mutated.bytes)) == vectors[4].size);
    assert(aw_decode(mutated.bytes, vectors[4].size, &frame));
    assert(frame.payload.telemetry.angle_mrad == INT32_MIN);
    assert(frame.payload.telemetry.velocity_mrad_s == INT32_MAX);
    assert(aw_decode(vectors[5].bytes, vectors[5].size, &frame));
    frame.payload.telemetry.status_flags |= AW_ACK_VALID;
    assert(aw_encode(&frame, mutated.bytes, sizeof(mutated.bytes)) == 0);
}

static size_t parse(AwParser *parser, const uint8_t *bytes, size_t size, uint32_t now,
                    uint8_t *types, size_t capacity)
{
    size_t i, count = 0;
    AwFrame frame;
    for (i = 0; i < size; ++i) {
        bool available = aw_parser_feed(parser, bytes[i], now, &frame);
        for (; available; available = aw_parser_next(parser, now, &frame)) {
            assert(count < capacity);
            types[count++] = frame.type;
        }
    }
    return count;
}

static void test_stream_recovery(void)
{
    AwParser parser;
    uint8_t expected[8];
    uint8_t chunk[128] = {0x8A, 0xA5, 0xA5, 0x5A, 1, AW_SESSION_OPEN, 0, 0, 8};
    size_t used = 9;
    size_t count;
    AwFrame frame;

    aw_parser_init(&parser);
    /* Bad TELEMETRY encloses a valid OPEN after its legitimate header. */
    memcpy(chunk + used, vectors[4].bytes, 7);
    memcpy(chunk + used + 7, vectors[0].bytes, vectors[0].size);
    if (aw_crc16(chunk + used + 2, 35) == 0) {
        chunk[used + 36] = 1;
    }
    used += 39;
    memcpy(chunk + used, vectors[1].bytes, vectors[1].size);
    used += vectors[1].size;
    assert(aw_decode(vectors[4].bytes, vectors[4].size, &frame));
    frame.payload.telemetry.angle_mrad = 0x5AA5; /* SYNC inside a good payload. */
    used += aw_encode(&frame, chunk + used, sizeof(chunk) - used);
    assert(parse(&parser, chunk, 1, 10, expected, COUNT(expected)) == 0);
    assert(parse(&parser, chunk + 1, 2, 11, expected, COUNT(expected)) == 0);
    count = parse(&parser, chunk + 3, used - 3, 12, expected, COUNT(expected));
    assert(count == 3);
    assert(expected[0] == AW_SESSION_OPEN && expected[1] == AW_SESSION_ACK
           && expected[2] == AW_TELEMETRY);
    assert(parser.rejected_headers == 1 && parser.rejected_crc == 1);

    /* Valid CRC but invalid payload is counted separately. */
    memcpy(chunk, vectors[0].bytes, vectors[0].size);
    memset(chunk + 7, 0, 8);
    recalc_crc(chunk, vectors[0].size);
    assert(parse(&parser, chunk, vectors[0].size, 13, expected, COUNT(expected)) == 0);
    assert(parser.rejected_payloads == 1);
}

static void test_timeout_and_wrap(void)
{
    AwParser parser;
    AwFrame frame;
    uint8_t types[2];
    uint32_t before_wrap = UINT32_MAX - 30;
    assert(aw_seq_is_new(0, UINT16_MAX));
    assert(!aw_seq_is_new(UINT16_MAX, 0) && !aw_seq_is_new(2, 2));
    assert(!aw_seq_is_new(0x8000, 0));
    assert(aw_sample_time_is_new(0, UINT32_MAX) && aw_elapsed_ms(4, before_wrap) == 35);
    assert(!aw_sample_time_is_new(3, 4) && !aw_sample_time_is_new(3, 3));
    assert(!aw_sample_time_is_new(UINT32_C(0x80000000), 0));

    aw_parser_init(&parser);
    assert(parse(&parser, vectors[0].bytes, 8, before_wrap, types, COUNT(types)) == 0);
    assert(!aw_parser_next(&parser, 68, &frame)); /* age 99, still pending */
    assert(parser.assembly_timeouts == 0);
    assert(!aw_parser_next(&parser, 69, &frame)); /* age 100 */
    assert(parser.used == 0 && parser.assembly_timeouts == 1);
    assert(parse(&parser, vectors[0].bytes + 8, vectors[0].size - 8,
                 70, types, COUNT(types)) == 0);
    assert(parse(&parser, vectors[0].bytes, vectors[0].size, 71, types, COUNT(types)) == 1);
    assert(types[0] == AW_SESSION_OPEN);

    /* If a valid suffix was enclosed in a truncated candidate, recover it. */
    aw_parser_init(&parser);
    assert(parse(&parser, vectors[4].bytes, 7, 1000, types, COUNT(types)) == 0);
    assert(parse(&parser, vectors[0].bytes, vectors[0].size, 1040, types, COUNT(types)) == 0);
    assert(aw_parser_next(&parser, 1100, &frame) && frame.type == AW_SESSION_OPEN);
    assert(parser.assembly_timeouts == 1);
}

static void test_capacity_guard(void)
{
    AwParser parser;
    AwFrame frame;
    uint8_t types[2];
    const Vector *open = &vectors[0];

    aw_parser_init(&parser);
    /* Deliberately construct an impossible cursor for the public drain call. */
    parser.used = SIZE_MAX;
    parser.rejected_headers = 2;
    assert(!aw_parser_next(&parser, 10, &frame));
    assert(parser.used == 0 && parser.buffer_overruns == 1 && parser.rejected_headers == 2);

    /* Deliberately fill the backing buffer before the next byte is fed. */
    parser.used = sizeof(parser.bytes);
    assert(!aw_parser_feed(&parser, open->bytes[0], 20, &frame));
    assert(parser.used == 1 && parser.buffer_overruns == 2);
    assert(parse(&parser, open->bytes + 1, open->size - 1, 21, types, COUNT(types)) == 1);
    assert(types[0] == AW_SESSION_OPEN && parser.used == 0);

    /* The feed path must also accept a new SYNC after an out-of-range cursor. */
    parser.used = sizeof(parser.bytes) + 1;
    assert(parse(&parser, open->bytes, open->size, 30, types, COUNT(types)) == 1);
    assert(parser.used == 0 && parser.buffer_overruns == 3 && parser.rejected_headers == 2);
}

static void test_long_noise_and_missed_drain(void)
{
    AwParser parser;
    AwFrame frame;
    uint8_t types[2];
    uint8_t malformed[3 * AW_MAX_FRAME];
    Vector previous_version = vectors[0];
    Vector bad_length = vectors[0];
    Vector bad_crc = vectors[0];
    const Vector *open = &vectors[0];
    const Vector *ack = &vectors[1];
    size_t used = 0;
    unsigned cycle;
    unsigned i;

    aw_parser_init(&parser);
    for (i = 0; i < 4096; ++i) {
        uint8_t byte = i % 3 == 0 ? 0xA5 : 'x';
        assert(!aw_parser_feed(&parser, byte, 10, &frame));
        assert(parser.used <= 1);
    }

    assert(parser.used == 1); /* The last noise byte is a partial SYNC. */

    previous_version.bytes[2] = 1;
    recalc_crc(previous_version.bytes, previous_version.size);
    bad_length.bytes[6] = AW_MAX_PAYLOAD;
    recalc_crc(bad_length.bytes, bad_length.size);
    bad_crc.bytes[bad_crc.size - 1] ^= 1;
    memcpy(malformed + used, previous_version.bytes, previous_version.size);
    used += previous_version.size;
    memcpy(malformed + used, bad_length.bytes, bad_length.size);
    used += bad_length.size;
    memcpy(malformed + used, bad_crc.bytes, bad_crc.size);
    used += bad_crc.size;

    for (cycle = 0; cycle < 32; ++cycle) {
        uint32_t now = 200u + 200u * cycle;
        assert(parse(&parser, malformed, used, now, types, COUNT(types)) == 0);
        /* Leave a truncated TELEMETRY holding one complete OPEN. On timeout,
         * feed returns OPEN while keeping the first byte of a following ACK. */
        assert(parse(&parser, vectors[4].bytes, 7, now, types, COUNT(types)) == 0);
        assert(parse(&parser, open->bytes, open->size, now + 20u, types, COUNT(types)) == 0);
        assert(aw_parser_feed(&parser, ack->bytes[0], now + 100u, &frame));
        assert(frame.type == AW_SESSION_OPEN && parser.used == 1);
        /* Deliberately skip next here and continue feeding one byte at a time. */
        for (i = 1; i < ack->size - 1; ++i) {
            assert(!aw_parser_feed(&parser, ack->bytes[i], now + 101u, &frame));
        }
        assert(aw_parser_feed(&parser, ack->bytes[ack->size - 1], now + 101u, &frame));
        assert(frame.type == AW_SESSION_ACK && parser.used == 0);
    }
    assert(parser.rejected_headers == 64 && parser.rejected_crc == 32);
    assert(parser.assembly_timeouts == 33 && parser.buffer_overruns == 0);
}

static AwFrame open_frame(uint64_t id)
{
    return (AwFrame){ .type = AW_SESSION_OPEN, .payload.session_id = id };
}

static AwFrame command_frame(uint64_t id, uint16_t seq, uint8_t mode)
{
    uint16_t target = mode == AW_STOP ? 0 : 600;
    return (AwFrame){ .type = AW_BRAKE_COMMAND, .seq = seq,
                      .payload.command = { id, mode, target, 500 } };
}

static AwSessionResult handle(AwSession *session, AwFrame frame, uint32_t now, uint16_t flags)
{
    AwFrame reply;
    AwSessionResult result = aw_session_handle(session, &frame, now, flags, 550, 15, &reply);
    if (result == AW_SEND_ACK) {
        assert(reply.type == AW_SESSION_ACK && reply.payload.session_id == session->id);
        assert(reply.seq == 15);
    }
    return result;
}

static void test_session_authorization(void)
{
    AwSession session;
    AwTelemetry sample = {0};
    aw_session_init(&session);
    aw_session_set_telemetry(&session, &sample);
    assert(sample.session_id == 0 && sample.status_flags == 0);
    assert(handle(&session, open_frame(SESSION), 1, GOOD_FLAGS) == AW_SEND_ACK);
    assert(!aw_session_authorize(&session, 2, GOOD_FLAGS));
    assert(handle(&session, command_frame(SESSION, 65535, AW_ENABLE), 3, GOOD_FLAGS) == AW_IGNORE);
    assert(handle(&session, command_frame(SESSION, 65535, AW_STOP), 4, AW_FAULT) == AW_ACCEPT_COMMAND);
    assert(!aw_session_authorize(&session, 5, GOOD_FLAGS | AW_ESTOP));
    assert(!aw_session_authorize(&session, 5, GOOD_FLAGS | 0x8000u));
    assert(aw_session_authorize(&session, 6, GOOD_FLAGS));
    assert(handle(&session, command_frame(SESSION, 0, AW_ENABLE), 7, GOOD_FLAGS) == AW_ACCEPT_COMMAND);
    assert(handle(&session, open_frame(SESSION), 8, GOOD_FLAGS) == AW_SEND_ACK);
    aw_session_set_telemetry(&session, &sample);
    assert(sample.applied_target_mnm == 550 && sample.last_command_seq == 0);
    assert((sample.status_flags & (AW_ACK_VALID | AW_ARM_READY)) == (AW_ACK_VALID | AW_ARM_READY));

    aw_session_tick(&session, 9, GOOD_FLAGS | AW_FAULT);
    assert(!session.arm_ready && session.applied_target_mnm == 0);
    assert(handle(&session, command_frame(SESSION, 1, AW_ENABLE), 10, GOOD_FLAGS) == AW_IGNORE);
    assert(aw_session_authorize(&session, 11, GOOD_FLAGS));
    assert(handle(&session, command_frame(SESSION, 1, AW_ENABLE), 12, GOOD_FLAGS) == AW_ACCEPT_COMMAND);
    assert(handle(&session, open_frame(SESSION + 1), 13, GOOD_FLAGS) == AW_SEND_ACK);
    assert(!session.arm_ready && !session.command_seen && session.applied_target_mnm == 0);
    assert(handle(&session, command_frame(SESSION, 2, AW_STOP), 14, GOOD_FLAGS) == AW_IGNORE);
    assert(handle(&session, command_frame(SESSION + 1, 0, AW_STOP), 15, GOOD_FLAGS) == AW_ACCEPT_COMMAND);
    assert(!session.arm_ready);
}

static void test_watchdog_and_reset(void)
{
    AwSession session;
    AwTelemetry sample = {0};
    uint32_t started = UINT32_MAX - 100;

    aw_session_init(&session);
    assert(handle(&session, open_frame(SESSION), started, GOOD_FLAGS) == AW_SEND_ACK);
    assert(handle(&session, command_frame(SESSION, 40000, AW_STOP), started, GOOD_FLAGS)
           == AW_ACCEPT_COMMAND);
    assert(aw_session_authorize(&session, 50, GOOD_FLAGS));
    assert(handle(&session, command_frame(SESSION, 40000, AW_STOP), 96, GOOD_FLAGS) == AW_IGNORE);
    assert(handle(&session, command_frame(SESSION + 9, 40001, AW_STOP), 97, GOOD_FLAGS) == AW_IGNORE);
    assert(handle(&session, open_frame(SESSION), 98, GOOD_FLAGS) == AW_SEND_ACK);
    aw_session_tick(&session, 99, GOOD_FLAGS); /* 200 ms across uint32 wrap. */
    aw_session_set_telemetry(&session, &sample);
    assert(sample.session_id == 0 && sample.applied_target_mnm == 0);
    assert(sample.status_flags == AW_CMD_TIMEOUT && sample.last_command_seq == 0);
    assert(handle(&session, open_frame(SESSION), 100, GOOD_FLAGS) == AW_IGNORE);
    assert(handle(&session, open_frame(SESSION + 1), 101, GOOD_FLAGS) == AW_SEND_ACK);
    assert(session.cmd_timeout && !aw_session_authorize(&session, 102, GOOD_FLAGS));
    assert(handle(&session, command_frame(SESSION + 1, 0, AW_STOP), 103, AW_FAULT) == AW_ACCEPT_COMMAND);
    assert(!session.cmd_timeout && !session.arm_ready);
    assert(handle(&session, command_frame(SESSION + 1, 1, AW_ENABLE), 104, GOOD_FLAGS) == AW_IGNORE);

    aw_session_init(&session); /* MCU reset independent of the PC. */
    aw_session_set_telemetry(&session, &sample);
    assert(sample.session_id == 0 && sample.status_flags == 0);
    assert(handle(&session, command_frame(SESSION + 1, 1, AW_STOP), 0, GOOD_FLAGS) == AW_IGNORE);
    assert(handle(&session, open_frame(SESSION + 2), 1, GOOD_FLAGS) == AW_SEND_ACK);
    assert(handle(&session, command_frame(SESSION + 2, 0, AW_STOP), 2, GOOD_FLAGS) == AW_ACCEPT_COMMAND);
}

int main(int argc, char **argv)
{
    load_vectors(argc > 1 ? argv[1] : "golden_vectors.txt");
    test_golden_vectors();
    puts("PASS: shared golden wire vectors (6/6)");
    test_strict_format();
    puts("PASS: strict framing, CRC, payload validation and signed units");
    test_stream_recovery();
    puts("PASS: split/concatenated input, noise, embedded sync and resynchronization");
    test_timeout_and_wrap();
    puts("PASS: assembly timeout and uint16/uint32 wrap");
    test_capacity_guard();
    puts("PASS: synthetic full/out-of-range buffer state and resynchronization");
    test_long_noise_and_missed_drain();
    puts("PASS: long noise, consecutive malformed frames and missed drain recovery");
    test_session_authorization();
    puts("PASS: session STOP/ACK, cap and independent local permit");
    test_watchdog_and_reset();
    puts("PASS: watchdog expiry, new session and MCU reset");
    return EXIT_SUCCESS;
}
