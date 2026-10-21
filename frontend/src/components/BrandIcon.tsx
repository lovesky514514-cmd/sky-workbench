import icon32 from '../assets/brand/workbench-icon-32.png'
import icon64 from '../assets/brand/workbench-icon-64.png'

export function BrandIcon({ size = 32, className = '', label = '' }: { size?: number; className?: string; label?: string }) {
  return <img
    className={'workbench-brand-icon ' + className}
    src={icon32}
    srcSet={`${icon32} 1x, ${icon64} 2x`}
    width={size}
    height={size}
    alt={label}
    aria-hidden={label ? undefined : true}
    draggable={false}
  />
}
