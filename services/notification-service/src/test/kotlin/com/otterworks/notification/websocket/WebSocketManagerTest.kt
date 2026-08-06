package com.otterworks.notification.websocket

import com.otterworks.notification.support.Fixtures
import com.otterworks.notification.support.RandomOrderRunner
import io.ktor.websocket.DefaultWebSocketSession
import io.ktor.websocket.Frame
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.runner.RunWith
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

@RunWith(RandomOrderRunner::class)
class WebSocketManagerTest {

    private fun session(): DefaultWebSocketSession = mockk(relaxed = true)

    @Test
    fun `pushing to a user with no session delivers nothing`() = runTest {
        val manager = WebSocketManager()

        assertEquals(0, manager.pushNotification("user-a", Fixtures.notification()))
        assertFalse(manager.isUserConnected("user-a"))
    }

    @Test
    fun `pushing reaches every session the user has open`() = runTest {
        val manager = WebSocketManager()
        val first = session()
        val second = session()
        manager.addConnection("user-a", first)
        manager.addConnection("user-a", second)

        assertEquals(2, manager.pushNotification("user-a", Fixtures.notification()))
        coVerify(exactly = 1) { first.send(any<Frame>()) }
        coVerify(exactly = 1) { second.send(any<Frame>()) }
    }

    @Test
    fun `a push never reaches another user's session`() = runTest {
        val manager = WebSocketManager()
        val theirs = session()
        manager.addConnection("user-b", theirs)

        assertEquals(0, manager.pushNotification("user-a", Fixtures.notification(userId = "user-a")))
        coVerify(exactly = 0) { theirs.send(any<Frame>()) }
    }

    @Test
    fun `registering the same session twice counts as one connection`() = runTest {
        val manager = WebSocketManager()
        val only = session()
        manager.addConnection("user-a", only)
        manager.addConnection("user-a", only)

        assertEquals(1, manager.pushNotification("user-a", Fixtures.notification()))
        assertEquals(1, manager.getConnectedUserCount())
    }

    @Test
    fun `closing the last session removes the user`() {
        val manager = WebSocketManager()
        val only = session()
        manager.addConnection("user-a", only)
        manager.removeConnection("user-a", only)

        assertFalse(manager.isUserConnected("user-a"))
        assertEquals(0, manager.getConnectedUserCount())
    }

    @Test
    fun `closing one of two sessions keeps the user connected`() {
        val manager = WebSocketManager()
        val first = session()
        val second = session()
        manager.addConnection("user-a", first)
        manager.addConnection("user-a", second)
        manager.removeConnection("user-a", first)

        assertTrue(manager.isUserConnected("user-a"))
        assertEquals(1, manager.getConnectedUserCount())
    }

    @Test
    fun `removing a session that was never registered is a no-op`() {
        val manager = WebSocketManager()

        manager.removeConnection("user-a", session())

        assertFalse(manager.isUserConnected("user-a"))
    }

    @Test
    fun `a session that fails to accept a frame is dropped`() = runTest {
        val manager = WebSocketManager()
        val dead = session()
        coEvery { dead.send(any<Frame>()) } throws RuntimeException("socket closed")
        manager.addConnection("user-a", dead)

        assertEquals(0, manager.pushNotification("user-a", Fixtures.notification()))
        assertFalse(manager.isUserConnected("user-a"))
    }

    @Test
    fun `a healthy session still receives a frame when a sibling session is dead`() = runTest {
        val manager = WebSocketManager()
        val dead = session()
        val alive = session()
        coEvery { dead.send(any<Frame>()) } throws RuntimeException("socket closed")
        manager.addConnection("user-a", dead)
        manager.addConnection("user-a", alive)

        assertEquals(1, manager.pushNotification("user-a", Fixtures.notification()))
        coVerify(exactly = 1) { alive.send(any<Frame>()) }
        assertTrue(manager.isUserConnected("user-a"))
    }

    @Test
    fun `connected user count tracks distinct users`() {
        val manager = WebSocketManager()
        manager.addConnection("user-a", session())
        manager.addConnection("user-b", session())

        assertEquals(2, manager.getConnectedUserCount())
    }
}
