/** Without this, Next.js never compiles the @tailwind directives and the page renders unstyled. */
const config = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}

export default config
