interface LoadingStateProps {
  message?: string;
}

export function LoadingState({ message = 'Loading industrial sites…' }: LoadingStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        minHeight: 300,
        gap: 20,
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      }}
    >
      {/* Double-ring spinner */}
      <div style={{ position: 'relative', width: 52, height: 52 }}>
        <div
          style={{
            position: 'absolute', inset: 0, borderRadius: '50%',
            border: '3px solid #e2e8f0',
            borderTopColor: '#0ea5e9',
            animation: 'spin 0.85s linear infinite',
          }}
        />
        <div
          style={{
            position: 'absolute', inset: 8, borderRadius: '50%',
            border: '3px solid #e2e8f0',
            borderTopColor: '#38bdf8',
            animation: 'spin 1.3s linear infinite reverse',
          }}
        />
        <div
          style={{
            position: 'absolute', inset: 16, borderRadius: '50%',
            background: 'linear-gradient(135deg, #e0f2fe, #bae6fd)',
          }}
        />
      </div>

      <div style={{ textAlign: 'center' }}>
        <p style={{ fontSize: 14, fontWeight: 600, color: '#1e293b', margin: '0 0 4px' }}>
          {message}
        </p>
        <p style={{ fontSize: 12, color: '#94a3b8', margin: 0 }}>
          Querying GIS backend…
        </p>
      </div>
    </div>
  );
}
