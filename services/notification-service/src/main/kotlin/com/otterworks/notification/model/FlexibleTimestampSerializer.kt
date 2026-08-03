package com.otterworks.notification.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.time.Instant
import java.time.format.DateTimeFormatter

/**
 * Accepts timestamps as either RFC 3339 strings or Unix epoch numbers
 * (seconds or milliseconds), normalizing numeric values to ISO-8601 strings.
 */
object FlexibleTimestampSerializer : KSerializer<String> {
    private const val EPOCH_MILLIS_THRESHOLD = 1_000_000_000_000L

    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleTimestamp", PrimitiveKind.STRING)

    override fun deserialize(decoder: Decoder): String {
        val jsonDecoder = decoder as? JsonDecoder ?: return decoder.decodeString()
        val primitive = jsonDecoder.decodeJsonElement().jsonPrimitive
        if (primitive.isString) return primitive.content
        val epoch = primitive.longOrNull ?: primitive.content.toDouble().toLong()
        val instant = if (epoch >= EPOCH_MILLIS_THRESHOLD) {
            Instant.ofEpochMilli(epoch)
        } else {
            Instant.ofEpochSecond(epoch)
        }
        return DateTimeFormatter.ISO_INSTANT.format(instant)
    }

    override fun serialize(encoder: Encoder, value: String) {
        encoder.encodeString(value)
    }
}
