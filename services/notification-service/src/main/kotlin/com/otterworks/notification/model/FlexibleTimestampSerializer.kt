package com.otterworks.notification.model

import java.time.Instant
import kotlinx.serialization.KSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.jsonPrimitive

/**
 * Accepts timestamps as either RFC 3339 strings or Unix epoch numbers
 * (seconds or milliseconds), normalizing numeric values to RFC 3339.
 * Legacy events from older service versions emit epoch integers.
 */
object FlexibleTimestampSerializer : KSerializer<String> {
    private const val EPOCH_MILLIS_THRESHOLD = 100_000_000_000L

    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleTimestamp", PrimitiveKind.STRING)

    override fun deserialize(decoder: Decoder): String {
        val jsonDecoder = decoder as? JsonDecoder
            ?: return decoder.decodeString()
        val primitive = jsonDecoder.decodeJsonElement().jsonPrimitive
        if (primitive.isString) return primitive.content
        val epoch = primitive.content.toLongOrNull()
            ?: primitive.content.toDoubleOrNull()?.toLong()
            ?: return primitive.content
        val instant = if (epoch >= EPOCH_MILLIS_THRESHOLD) {
            Instant.ofEpochMilli(epoch)
        } else {
            Instant.ofEpochSecond(epoch)
        }
        return instant.toString()
    }

    override fun serialize(encoder: Encoder, value: String) {
        encoder.encodeString(value)
    }
}
