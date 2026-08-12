interface Map3DToggleProps {
  is3DOpen: boolean;
  onToggle: () => void;
}

export default function Map3DToggle({ is3DOpen, onToggle }: Map3DToggleProps) {
  const label = is3DOpen ? '2D로 돌아가기' : '3D 보기';

  return (
    <button
      type="button"
      className={`map3dToggle${is3DOpen ? ' isOpen' : ''}`}
      aria-label={label}
      aria-pressed={is3DOpen}
      onClick={onToggle}
    >
      {label}
    </button>
  );
}
