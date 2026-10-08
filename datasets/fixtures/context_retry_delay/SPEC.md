retry_delay(headers): read Retry-After as numeric seconds; clamp valid finite values to [0, 30]. Missing, invalid, NaN or infinite values use 2.0 seconds. Do not change headers.
