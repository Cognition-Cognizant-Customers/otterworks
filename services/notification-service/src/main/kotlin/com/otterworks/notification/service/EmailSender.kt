package com.otterworks.notification.service

import aws.sdk.kotlin.services.ses.SesClient
import aws.sdk.kotlin.services.ses.model.Body
import aws.sdk.kotlin.services.ses.model.Content
import aws.sdk.kotlin.services.ses.model.Destination
import aws.sdk.kotlin.services.ses.model.Message
import aws.sdk.kotlin.services.ses.model.SendEmailRequest
import com.otterworks.notification.config.AppConfig
import io.github.oshai.kotlinlogging.KotlinLogging

private val logger = KotlinLogging.logger {}

class EmailSender(
    private val sesClient: SesClient,
    private val config: AppConfig,
) {

    suspend fun sendEmail(
        toAddress: String,
        subject: String,
        htmlBody: String,
    ): Boolean {
        return try {
            val request = SendEmailRequest {
                source = config.sesFromEmail
                destination = Destination {
                    toAddresses = listOf(toAddress)
                }
                message = Message {
                    this.subject = Content {
                        data = subject
                        charset = "UTF-8"
                    }
                    body = Body {
                        html = Content {
                            data = htmlBody
                            charset = "UTF-8"
                        }
                    }
                }
            }

            val response = sesClient.sendEmail(request)
            logger.info { "Email sent to $toAddress, messageId=${response.messageId}" }
            true
        } catch (e: Exception) {
            logger.error(e) { "Failed to send email to $toAddress" }
            false
        }
    }
}
