/**
 * The Media Convert mark: a play triangle with a download arrow cut out of it.
 * Decorative here, since it always sits beside the name. Keep the shapes in
 * step with public/favicon.svg.
 */
export function Logo() {
  return (
    <svg class="logo" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <mask id="logo-arrow">
        <rect width="64" height="64" fill="#fff" />
        <path
          d="M29 20v17M22.5 31l6.5 6.5 6.5-6.5M23 44h12"
          fill="none"
          stroke="#000"
          stroke-width="4.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </mask>
      <path
        d="M17 11l37 21-37 21z"
        fill="currentColor"
        stroke="currentColor"
        stroke-width="7"
        stroke-linejoin="round"
        mask="url(#logo-arrow)"
      />
    </svg>
  );
}
