import React from 'react'

export const VarunaIcon = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
    <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2.5" />
    <circle cx="12" cy="12" r="7" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 2" />
    <circle cx="12" cy="12" r="10.5" stroke="currentColor" strokeWidth="1" strokeDasharray="1 3" opacity="0.4" />
    <path
      d="M12 2V4M12 20V22M2 12H4M20 12H22M4.93 4.93L6.34 6.34M17.66 17.66L19.07 19.07M4.93 19.07L6.34 17.66M17.66 6.34L19.07 4.93"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </svg>
)
