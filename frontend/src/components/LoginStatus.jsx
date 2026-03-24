export default function LoginStatus({ auth }) {
  if (!auth.checked) {
    return (
      <div className="auth-badge">
        <span className="auth-dot loading" />
        Connecting...
      </div>
    );
  }

  if (auth.ok) {
    return (
      <div className="auth-badge">
        <span className="auth-dot ok" />
        Connected
      </div>
    );
  }

  return (
    <div className="auth-badge">
      <span className="auth-dot fail" />
      Disconnected
    </div>
  );
}
