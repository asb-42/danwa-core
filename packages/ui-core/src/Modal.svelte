<script>
  export let open = false;
  export let title = '';
  export let size = 'md'; // sm, md, lg, xl, full
  export let closeOnOverlayClick = true;

  const sizeClasses = {
    sm: 'max-w-sm',
    md: 'max-w-md',
    lg: 'max-w-lg',
    xl: 'max-w-xl',
    full: 'max-w-4xl',
  };
</script>

{#if open}
  <div class="fixed inset-0 z-50 overflow-y-auto">
    <div class="flex min-h-full items-center justify-center p-4">
      <div
        class="fixed inset-0 bg-gray-900/50 transition-opacity"
        on:click={() => closeOnOverlayClick && (open = false)}
      ></div>

      <div class="relative w-full {sizeClasses[size]} bg-white rounded-lg shadow-xl transform transition-all">
        {#if title}
          <div class="flex items-center justify-between p-4 border-b border-gray-200">
            <h3 class="text-lg font-semibold text-gray-900">{title}</h3>
            <button
              on:click={() => open = false}
              class="text-gray-400 hover:text-gray-600"
            >
              <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        {/if}

        <div class="p-4">
          <slot></slot>
        </div>
      </div>
    </div>
  </div>
{/if}