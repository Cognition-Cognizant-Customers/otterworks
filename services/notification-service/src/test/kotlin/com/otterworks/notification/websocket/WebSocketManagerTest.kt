package com.otterworks.notification.websocket

import com.otterworks.notification.model.Notification
import io.ktor.websocket.DefaultWebSocketSession
import io.ktor.websocket.Frame
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Fan-out and connection bookkeeping for the push channel. Sessions are mocked, so the
 * only timing involved is the coroutine machinery — no sockets, no sleeps.
 */
class WebSocketManagerTest {

    private val manager = WebSocketManager()

    private val notification = Notification(
        id = "n-1",
        userId = "user-1",
        type = "file_shared",
        title = "File Shared With You",
        message = "A file has been shared with you.",
        createdAt = "2024-01-01T00:00:00Z",
    )

    private fun session() = mockk<DefaultWebSocketSession>(relaxed = true)

    // ── Positive ────────────────────────────────────────────────────────────────

    @Test
    fun `a registered session receives the notification payload`() = runTest {
        val session = session()
        manager.addConnection("user-1", session)

        assertEquals(1, manager.pushNotification("user-1", notification))

        val frame = slot<Frame>()
        coVerify(exactly = 1) { session.send(capture(frame)) }
        val text = String((frame.captured as Frame.Text).data)
        assertTrue(text.contains("\"id\":\"n-1\""))
        assertTrue(text.contains("\"userId\":\"user-1\""))
    }

    @Test
    fun `every session of the same user receives the notification`() = runTest {
        manager.addConnection("user-1", session())
        manager.addConnection("user-1", session())

        assertEquals(2, manager.pushNotification("user-1", notification))
        assertEquals(1, manager.getConnectedUserCount())
    }

    @Test
    fun `adding the same session twice does not duplicate the delivery`() = runTest {
        val session = session()
        manager.addConnection("user-1", session)
        manager.addConnection("user-1", session)

        assertEquals(1, manager.pushNotification("user-1", notification))
        coVerify(exactly = 1) { session.send(any()) }
    }

    // ── Negative ────────────────────────────────────────────────────────────────

    @Test
    fun `pushing to a user with no session delivers nothing`() = runTest {
        assertEquals(0, manager.pushNotification("nobody", notification))
        assertFalse(manager.isUserConnected("nobody"))
    }

    @Test
    fun `a session whose send fails is dropped and not counted as delivered`() = runTest {
        val dead = session()
        val live = session()
        coEvery { dead.send(any()) } throws RuntimeException("socket closed")
        manager.addConnection("user-1", dead)
        manager.addConnection("user-1", live)

        assertEquals(1, manager.pushNotification("user-1", notification))

        // The dead session is pruned, so a second push only reaches the live one.
        assertEquals(1, manager.pushNotification("user-1", notification))
        coVerify(exactly = 1) { dead.send(any()) }
        coVerify(exactly = 2) { live.send(any()) }
    }

    @Test
    fun `a user whose only session dies is no longer connected`() = runTest {
        val dead = session()
        coEvery { dead.send(any()) } throws RuntimeException("socket closed")
        manager.addConnection("user-1", dead)

        assertEquals(0, manager.pushNotification("user-1", notification))
        assertFalse(manager.isUserConnected("user-1"))
        assertEquals(0, manager.getConnectedUserCount())
    }

    @Test
    fun `a push never crosses to another user`() = runTest {
        val theirs = session()
        manager.addConnection("user-1", session())
        manager.addConnection("user-2", theirs)

        manager.pushNotification("user-1", notification)

        coVerify(exactly = 0) { theirs.send(any()) }
        assertEquals(2, manager.getConnectedUserCount())
    }

    // ── Connection bookkeeping / idempotency ────────────────────────────────────

    @Test
    fun `removing the last session removes the user entirely`() {
        val session = session()
        manager.addConnection("user-1", session)

        manager.removeConnection("user-1", session)

        assertFalse(manager.isUserConnected("user-1"))
        assertEquals(0, manager.getConnectedUserCount())
    }

    @Test
    fun `removing one of two sessions keeps the user connected`() {
        val first = session()
        manager.addConnection("user-1", first)
        manager.addConnection("user-1", session())

        manager.removeConnection("user-1", first)

        assertTrue(manager.isUserConnected("user-1"))
    }

    @Test
    fun `removing an unknown session is a no-op`() {
        manager.addConnection("user-1", session())

        manager.removeConnection("user-1", session())
        manager.removeConnection("user-unknown", session())

        assertTrue(manager.isUserConnected("user-1"))
        assertEquals(1, manager.getConnectedUserCount())
    }

    @Test
    fun `removing the same session twice is idempotent`() {
        val session = session()
        manager.addConnection("user-1", session)

        manager.removeConnection("user-1", session)
        manager.removeConnection("user-1", session)

        assertEquals(0, manager.getConnectedUserCount())
    }

    @Test
    fun `concurrent registrations for one user are all retained`() {
        // Real threads here on purpose: the registry is a ConcurrentHashMap of keySet views
        // and computeIfAbsent must not lose a racing registration. The assertion is on the
        // final count, not on any interleaving, so the outcome is deterministic.
        val sessions = List(64) { session() }
        runBlocking {
            sessions.map { session ->
                async(Dispatchers.Default) { manager.addConnection("user-1", session) }
            }.awaitAll()
        }

        assertEquals(1, manager.getConnectedUserCount())
        assertEquals(64, runBlocking { manager.pushNotification("user-1", notification) })
    }
}
