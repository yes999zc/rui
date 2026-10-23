<script setup lang="ts">
/**
 * Rui mark: three pixel sound bars on the left, two nodes on a data rail on the
 * right, and packets moving between them. Flat 1-bit pixels (crispEdges) stay
 * sharp at 20–28 px; the loop is CSS, so the page ships no GIF or heavy asset.
 */
withDefaults(defineProps<{ size?: number; animated?: boolean }>(), {
  size: 30,
  animated: true,
})
</script>

<template>
  <svg
    class="brand-mark"
    :class="{ 'is-animated': animated }"
    :width="size"
    :height="size"
    viewBox="0 0 32 32"
    shape-rendering="crispEdges"
    aria-hidden="true"
    focusable="false"
  >
    <!-- sound / ear side -->
    <rect class="bar b1" x="3" y="13" width="3" height="6" />
    <rect class="bar b2" x="8" y="9" width="3" height="14" />
    <rect class="bar b3" x="13" y="11" width="3" height="10" />
    <!-- data rail and nodes -->
    <rect class="rail" x="18" y="15" width="10" height="2" />
    <rect class="node" x="17" y="13" width="4" height="6" />
    <rect class="node node-end" x="27" y="12" width="4" height="8" />
    <!-- packets travel left -> right along the rail -->
    <rect class="packet p1" x="19" y="14" width="3" height="4" />
    <rect class="packet p2" x="19" y="14" width="3" height="4" />
    <rect class="packet p3" x="19" y="14" width="3" height="4" />
  </svg>
</template>

<style scoped>
.brand-mark {
  flex: none;
  overflow: visible;
}

.bar {
  fill: var(--accent);
}

.b1 {
  opacity: 0.55;
}

.b3 {
  opacity: 0.78;
}

.rail {
  fill: var(--line-strong);
}

.node {
  fill: var(--accent);
}

.node-end {
  fill: var(--live);
}

.packet {
  fill: var(--live);
  opacity: 0;
}

.is-animated .packet {
  animation: packet-flow 2.2s steps(5, end) infinite;
}

.is-animated .p2 {
  animation-delay: 0.73s;
}

.is-animated .p3 {
  animation-delay: 1.46s;
}

.is-animated .node-end {
  animation: node-blink 2.2s steps(2, end) infinite;
}

@keyframes packet-flow {
  0% {
    opacity: 0;
    transform: translateX(0);
  }
  14% {
    opacity: 1;
  }
  62% {
    opacity: 1;
    transform: translateX(6px);
  }
  78%,
  100% {
    opacity: 0;
    transform: translateX(7px);
  }
}

@keyframes node-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.25;
  }
}

@media (prefers-reduced-motion: reduce) {
  .is-animated .packet,
  .is-animated .node-end {
    animation: none;
  }

  .is-animated .p1 {
    opacity: 0.85;
  }

  .is-animated .p2 {
    opacity: 0.7;
    transform: translateX(2px);
  }

  .is-animated .p3 {
    opacity: 0.55;
    transform: translateX(4px);
  }
}
</style>
