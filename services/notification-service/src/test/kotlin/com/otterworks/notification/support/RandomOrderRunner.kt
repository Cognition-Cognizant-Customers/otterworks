package com.otterworks.notification.support

import org.junit.runner.notification.RunNotifier
import org.junit.runners.BlockJUnit4ClassRunner
import org.junit.runners.model.FrameworkMethod
import java.util.Random

/**
 * JUnit 4 runner that executes the methods of a test class in a random order.
 *
 * The seed is taken from the `TEST_SEED` environment variable when present, so an ordering can
 * be replayed, and is printed with the resulting order on every run. Tests annotated with this
 * runner must not depend on execution order.
 */
class RandomOrderRunner(klass: Class<*>) : BlockJUnit4ClassRunner(klass) {

    // Called from the superclass constructor during validation, so this must not touch
    // instance state initialised after construction.
    override fun computeTestMethods(): List<FrameworkMethod> =
        super.computeTestMethods().sortedBy { it.name }.toMutableList().apply { shuffle(Random(seed)) }

    override fun run(notifier: RunNotifier) {
        println(
            "[RandomOrderRunner] ${testClass.name} seed=$seed " +
                "order=${computeTestMethods().joinToString { it.name }}"
        )
        super.run(notifier)
    }

    private companion object {
        val seed: Long = System.getenv("TEST_SEED")?.toLongOrNull() ?: Random().nextLong()
    }
}
