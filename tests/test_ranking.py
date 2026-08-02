from momo.domain.ranking import score_news_item


def test_notice_related_scores_higher_than_generic():
    notice = score_news_item(
        news_sub_type="NOTICE",
        view_count=100,
        publish_time="",
        related_securities=["MY.1155"],
        symbol="MY.1155",
        stock_name="Maybank",
        title="Maybank quarterly results",
    )
    generic = score_news_item(
        news_sub_type="NEWS",
        view_count=0,
        publish_time="",
        related_securities=[],
        symbol="MY.1155",
        stock_name="Maybank",
        title="Market wraps up",
    )
    assert notice > generic
