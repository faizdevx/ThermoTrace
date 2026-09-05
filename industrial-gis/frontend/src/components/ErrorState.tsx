interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        minHeight: 300,
        gap: 16,
        padding: 32,
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      }}
    >
      <div
        style={{
          width: 60, height: 60, borderRadius: '50%',
          background: '#fef2f2',
          border: '2px solid #fca5a5',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 26,
        }}
      >
        ⚠️
      </div>

      <div style={{ textAlign: 'center', maxWidth: 400 }}>
        <p style={{ fontSize: 15, fontWeight: 700, color: '#1e293b', margin: '0 0 8px' }}>
          Unable to load data
        </p>
        <p style={{ fontSize: 13, color: '#64748b', margin: 0, lineHeight: 1.6 }}>
          {message}
        </p>
      </div>

      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            padding: '8px 22px',
            borderRadius: 10,
            border: 'none',
            background: '#0f172a',
            color: '#fff',
            fontSize: 13,
            fontWeight: 600,
            cursor: 'pointer',
            transition: 'background 0.2s',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = '#1e293b')}
          onMouseLeave={e => (e.currentTarget.style.background = '#0f172a')}
        >
          Try Again
        </button>
      )}
    </div>
  );
}
