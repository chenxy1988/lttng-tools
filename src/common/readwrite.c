/*
 * Copyright (C) 2013 - Mathieu Desnoyers <mathieu.desnoyers@efficios.com>
 *
 * This library is free software; you can redistribute it and/or modify it
 * under the terms of the GNU Lesser General Public License, version 2.1 only,
 * as published by the Free Software Foundation.
 *
 * This library is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License
 * for more details.
 *
 * You should have received a copy of the GNU Lesser General Public License
 * along with this library; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
 */

#include <unistd.h>
#include "readwrite.h"

/*
 * lttng_read and lttng_write take care of EINTR and partial read/write.
 * Upon success, they return the "count" received as parameter.
 * They can return a negative value if an error occurs.
 * If a value lower than the requested "count" is returned, it means an
 * error occured.
 * The error can be checked by querying errno.
 */
ssize_t lttng_read(int fd, void *buf, size_t count)
{
	size_t i = 0;
	ssize_t ret;

	do {
		ret = read(fd, &buf[i], count - i);
		if (ret < 0) {
			if (errno == EINTR) {
				continue;	/* retry operation */
			} else {
				goto error;
			}
		}
		i += ret;
		assert(i <= count);
	} while (count - i > 0 && ret > 0);
	return i;

error:
	if (i == 0) {
		return -1;
	} else {
		return i;
	}
}

ssize_t lttng_write(int fd, const void *buf, size_t count)
{
	size_t i = 0;
	ssize_t ret;

	do {
		ret = write(fd, &buf[i], count - i);
		if (ret < 0) {
			if (errno == EINTR) {
				continue;	/* retry operation */
			} else {
				goto error;
			}
		}
		i += ret;
		assert(i <= count);
	} while (count - i > 0 && ret > 0);
	return i;

error:
	if (i == 0) {
		return -1;
	} else {
		return i;
	}
}
