import { Construction } from 'lucide-react';

interface ComingSoonProps {
  title: string;
  description?: string;
}

export default function ComingSoon({ title, description }: ComingSoonProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
      <Construction className="h-16 w-16 text-muted-foreground/40 mb-4" />
      <h2 className="text-2xl font-semibold text-foreground mb-2">{title}</h2>
      <p className="text-muted-foreground max-w-md">
        {description || '该功能正在开发中，敬请期待。'}
      </p>
    </div>
  );
}
