import { useParams } from 'react-router-dom';
import QualityReview from './admin/QualityReview';

export default function WorkspaceQualityReview() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  return <QualityReview workspaceId={Number(workspaceId) || 0} />;
}
