/*
 * Copyright (C) 2020 Simon Marchi <simon.marchi@efficios.com>
 *
 * SPDX-License-Identifier: GPL-2.0-only
 *
 */

#ifndef COMMON_SNAPSHOT_H
#define COMMON_SNAPSHOT_H

#include <common/macros.h>

#include <stdbool.h>

struct lttng_buffer_view;
struct lttng_dynamic_buffer;
struct lttng_snapshot_output;

LTTNG_HIDDEN
bool lttng_snapshot_output_validate(const struct lttng_snapshot_output *output);

LTTNG_HIDDEN
bool lttng_snapshot_output_is_equal(
		const struct lttng_snapshot_output *a,
		const struct lttng_snapshot_output *b);

LTTNG_HIDDEN
int lttng_snapshot_output_serialize(
		const struct lttng_snapshot_output *output,
		struct lttng_dynamic_buffer *buf);

LTTNG_HIDDEN
ssize_t lttng_snapshot_output_create_from_buffer(
		const struct lttng_buffer_view *view,
		struct lttng_snapshot_output **output_p);

#endif /* COMMON_SNAPSHOT_H */