<script>
  export let variant = 'default'; // default, bordered, elevated
  export let padding = 'md'; // none, sm, md, lg

  const variantClasses = {
    default: 'bg-white',
    bordered: 'bg-white border border-gray-200',
    elevated: 'bg-white shadow-lg',
  };

  const paddingClasses = {
    none: '',
    sm: 'p-4',
    md: 'p-6',
    lg: 'p-8',
  };
</script>

<div class={variantClasses[variant] + ' rounded-lg ' + paddingClasses[padding]}>
  <slot></slot>
</div>