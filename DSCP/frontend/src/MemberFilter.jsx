export function MemberFilter({ roster, value, onChange, me }) {
  return (
    <select
      className="field"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
    >
      <option value="">{roster.length > 1 ? "All family" : "Everyone"}</option>
      {roster.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name}{m.id === me.id ? " (you)" : ""}
        </option>
      ))}
    </select>
  );
}
