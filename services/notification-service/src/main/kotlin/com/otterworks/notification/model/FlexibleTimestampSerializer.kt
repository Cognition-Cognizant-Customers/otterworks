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
import kotlinx.serialization.json.longOrNull

/**
 * Accepts timestamps as either RFC 3339 strings or Unix epoch numbers
 * (seconds or milliseconds), normalizing epoch values to ISO-8601 strings.
 * Legacy producers emit epoch integers while newer ones emit RFC 3339 strings.
 */
object FlexibleTimestampSerializer : KSerializer<String> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleTimestamp", PrimitiveKind.STRING)

    private const val EPOCH_MILLIS_THRESHOLD = 100_000_000_000L

    override fun deserialize(decoder: Decoder): String {
        if (decoder is JsonDecoder) {
            val element = decoder.decodeJsonElement()
            val primitive = element.jsonPrimitive
            val epoch = if (primitive.isString) null else primitive.longOrNull
            return if (epoch != null) {
                val instant = if (epoch >= EPOCH_MILLIS_THRESHOLD) {
                    Instant.ofEpochMilli(epoch)
                } else {
                    Instant.ofEpochSecond(epoch)
                }
                instant.toString()
            } else {
                primitive.content
            }
        }
        return decoder.decodeString()
    }

    override fun serialize(encoder: Encoder, value: String) {
        encoder.encodeString(value)
    }
}
